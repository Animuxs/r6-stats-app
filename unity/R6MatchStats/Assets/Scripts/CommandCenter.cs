using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Text;
using UnityEngine;
using UnityEngine.Networking;
using UnityEngine.UI;

namespace R6MatchIntelligence
{
    public sealed class CommandCenter : MonoBehaviour
    {
        private const string DefaultApi = "http://127.0.0.1:8000/api/v1";
        private static readonly string[] ModuleNames = {
            "Dashboard", "Match History", "Operator Analytics", "Team Analytics", "School Selection"
        };

        private Camera _camera;
        private RectTransform _pageBody;
        private InputField _apiInput;
        private InputField _pathInput;
        private Text _statusText;
        private Text _pageTitle;
        private string _apiBase;
        private string _activeModule = "Dashboard";
        private string _season = "current";
        private string _summaryJson = "";
        private string _historyJson = "";
        private string _schoolsJson = "";
        private string _selectedSchoolName = "";
        private string _selectedTeamName = "";
        private ParsedReplay _currentReplay;
        // the players picked to save from the parsed match: nobody until picked, since someone on
        // your team in Ranked may be a stranger
        private readonly HashSet<string> _pickedPlayers = new HashSet<string>();
        private readonly List<Renderer> _brandMeshes = new List<Renderer>();
        private Color _accent = new Color(0.83f, 0.54f, 0.30f);
        private RectTransform _activeTabPlate;
        private Rect _activeTabTarget;

        [RuntimeInitializeOnLoadMethod(RuntimeInitializeLoadType.AfterSceneLoad)]
        private static void Bootstrap()
        {
            if (FindObjectOfType<CommandCenter>() != null) return;
            var root = new GameObject("R6 Match Intelligence");
            DontDestroyOnLoad(root);
            root.AddComponent<CommandCenter>();
        }

        private void Awake()
        {
            Application.targetFrameRate = 60;
            _apiBase = PlayerPrefs.GetString("r6_api_base", DefaultApi).TrimEnd('/');
            CreateWorld();
            CreateInterface();
            StartCoroutine(RefreshData());
        }

        private void Update()
        {
            if (_camera == null) return;
            var mouse = new Vector2(Input.mousePosition.x / Mathf.Max(Screen.width, 1), Input.mousePosition.y / Mathf.Max(Screen.height, 1));
            var target = Quaternion.Euler(11f + (0.5f - mouse.y) * 2.2f, (mouse.x - 0.5f) * 3.5f, 0f);
            _camera.transform.rotation = Quaternion.Slerp(_camera.transform.rotation, target * Quaternion.Euler(11f, 0f, 0f), Time.deltaTime * 0.35f);
            if (_activeTabPlate != null)
            {
                var plate = _activeTabPlate.anchoredPosition;
                plate.x = Mathf.Lerp(plate.x, _activeTabTarget.x, Time.deltaTime * 12f);
                _activeTabPlate.anchoredPosition = plate;
            }
        }

        private void CreateWorld()
        {
            _camera = Camera.main;
            if (_camera == null)
            {
                var cameraObject = new GameObject("Command Center Camera");
                _camera = cameraObject.AddComponent<Camera>();
                cameraObject.tag = "MainCamera";
            }
            _camera.clearFlags = CameraClearFlags.SolidColor;
            _camera.backgroundColor = new Color(0.055f, 0.075f, 0.075f);
            _camera.fieldOfView = 44f;
            _camera.transform.position = new Vector3(0f, 3.4f, -10.5f);
            _camera.transform.rotation = Quaternion.Euler(11f, 0f, 0f);

            RenderSettings.ambientLight = new Color(0.42f, 0.48f, 0.45f);
            var key = new GameObject("Warm key light").AddComponent<Light>();
            key.type = LightType.Directional;
            key.color = _accent;
            key.intensity = 1.35f;
            key.transform.rotation = Quaternion.Euler(48f, -32f, 0f);
            var fill = new GameObject("Cyan fill light").AddComponent<Light>();
            fill.type = LightType.Point;
            fill.color = new Color(0.32f, 0.72f, 0.68f);
            fill.intensity = 9f;
            fill.range = 13f;
            fill.transform.position = new Vector3(4f, 3.2f, 1f);

            var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
            ground.name = "Tactical ground";
            ground.transform.position = new Vector3(0f, -0.62f, 0f);
            ground.transform.localScale = new Vector3(1.5f, 1f, 1.5f);
            ApplyMaterial(ground, new Color(0.085f, 0.12f, 0.12f), 0.75f);
            Destroy(ground.GetComponent<Collider>());

            var heights = new[] { .7f, 1.5f, .95f, .45f, 1.85f, .72f, 1.35f, .52f, 1.8f, .85f, .62f, 1.18f, .62f, 1.05f, .42f, 1.35f, .72f, 1.52f };
            for (var index = 0; index < heights.Length; index++)
            {
                var x = (index % 6) * 1.06f - 2.65f;
                var z = (index / 6) * 1.06f - 1.06f;
                var plate = GameObject.CreatePrimitive(PrimitiveType.Cube);
                plate.name = "Tactical plate " + index.ToString("00");
                plate.transform.position = new Vector3(x, heights[index] * 0.5f - 0.36f, z);
                plate.transform.localScale = new Vector3(0.78f, heights[index], 0.78f);
                var baseColor = index % 6 == 0 ? _accent : index % 3 == 0 ? new Color(0.22f, 0.43f, 0.40f) : new Color(0.17f, 0.22f, 0.22f);
                ApplyMaterial(plate, baseColor, 0.8f);
                if (index % 6 == 0) _brandMeshes.Add(plate.GetComponent<Renderer>());
                Destroy(plate.GetComponent<Collider>());
            }
        }

        private static void ApplyMaterial(GameObject target, Color color, float metallic)
        {
            var renderer = target.GetComponent<Renderer>();
            var shader = Shader.Find("Universal Render Pipeline/Lit") ?? Shader.Find("Standard");
            var material = new Material(shader) { color = color };
            if (material.HasProperty("_Metallic")) material.SetFloat("_Metallic", metallic);
            if (material.HasProperty("_Smoothness")) material.SetFloat("_Smoothness", 0.62f);
            renderer.material = material;
        }

        private void CreateInterface()
        {
            var canvasObject = new GameObject("Command Center UI", typeof(Canvas), typeof(CanvasScaler), typeof(GraphicRaycaster));
            var canvas = canvasObject.GetComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            var scaler = canvasObject.GetComponent<CanvasScaler>();
            scaler.uiScaleMode = CanvasScaler.ScaleMode.ScaleWithScreenSize;
            scaler.referenceResolution = new Vector2(1600f, 900f);
            scaler.matchWidthOrHeight = 0.5f;
            if (FindObjectOfType<UnityEngine.EventSystems.EventSystem>() == null)
                new GameObject("Event System", typeof(UnityEngine.EventSystems.EventSystem), typeof(UnityEngine.EventSystems.StandaloneInputModule));

            var root = canvasObject.GetComponent<RectTransform>();
            var topbar = CreatePanel(root, "Top navigation", new Vector2(0f, 1f), new Vector2(1f, 1f), new Vector2(0.5f, 1f), Vector2.zero, new Vector2(0f, 72f), new Color(0.06f, 0.085f, 0.085f, 0.97f));
            CreateText(topbar, "R6 / INTEL", new Vector2(28f, -17f), new Vector2(210f, 32f), 19, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
            CreateText(topbar, "TACTICAL ANALYTICS", new Vector2(30f, -48f), new Vector2(210f, 17f), 10, new Color(0.58f, 0.65f, 0.62f), FontStyle.Normal, TextAnchor.MiddleLeft);

            var tabs = new GameObject("Modules", typeof(RectTransform)).GetComponent<RectTransform>();
            tabs.SetParent(topbar, false);
            tabs.anchorMin = new Vector2(0f, 0f); tabs.anchorMax = new Vector2(1f, 1f); tabs.offsetMin = new Vector2(245f, 0f); tabs.offsetMax = new Vector2(-175f, 0f);
            var tabWidth = 164f;
            _activeTabPlate = CreatePanel(tabs, "Active module plate", new Vector2(0f, 0f), new Vector2(0f, 0f), new Vector2(0f, 0f), new Vector2(0f, 0f), new Vector2(tabWidth, 3f), _accent);
            for (var index = 0; index < ModuleNames.Length; index++)
            {
                var module = ModuleNames[index];
                var button = CreateButton(tabs, module, new Vector2(index * tabWidth, 0f), new Vector2(tabWidth, 72f), () => OpenModule(module));
                button.GetComponent<Image>().color = new Color(0f, 0f, 0f, 0f);
                button.GetComponentInChildren<Text>().fontSize = 13;
                if (module == _activeModule) _activeTabTarget = new Rect(index * tabWidth, 0f, tabWidth, 3f);
            }
            CreateText(topbar, "LOCAL API", new Vector2(-30f, -23f), new Vector2(135f, 22f), 10, new Color(0.55f, 0.75f, 0.66f), FontStyle.Bold, TextAnchor.MiddleRight);

            var frame = CreatePanel(root, "Main frame", new Vector2(0f, 0f), new Vector2(1f, 1f), new Vector2(0.5f, 0.5f), new Vector2(55f, 38f), new Vector2(-110f, -148f), new Color(0.065f, 0.09f, 0.09f, 0.90f));
            CreateText(frame, "COMMAND CENTER  /  PERFORMANCE INTELLIGENCE", new Vector2(27f, -20f), new Vector2(600f, 19f), 10, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
            _pageTitle = CreateText(frame, "Dashboard", new Vector2(27f, -47f), new Vector2(650f, 42f), 28, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
            _statusText = CreateText(frame, "Connecting to local statistics API...", new Vector2(-28f, -22f), new Vector2(380f, 20f), 10, new Color(0.62f, 0.72f, 0.67f), FontStyle.Normal, TextAnchor.MiddleRight);

            _pageBody = new GameObject("Page body", typeof(RectTransform)).GetComponent<RectTransform>();
            _pageBody.SetParent(frame, false);
            _pageBody.anchorMin = new Vector2(0f, 0f); _pageBody.anchorMax = new Vector2(1f, 1f); _pageBody.offsetMin = new Vector2(24f, 24f); _pageBody.offsetMax = new Vector2(-24f, -104f);
            DrawPage();
        }

        private RectTransform CreatePanel(RectTransform parent, string name, Vector2 anchorMin, Vector2 anchorMax, Vector2 pivot, Vector2 position, Vector2 size, Color color)
        {
            var panel = new GameObject(name, typeof(RectTransform), typeof(Image)).GetComponent<RectTransform>();
            panel.SetParent(parent, false); panel.anchorMin = anchorMin; panel.anchorMax = anchorMax; panel.pivot = pivot;
            panel.anchoredPosition = position; panel.sizeDelta = size; panel.GetComponent<Image>().color = color;
            return panel;
        }

        private Text CreateText(RectTransform parent, string value, Vector2 position, Vector2 size, int fontSize, Color color, FontStyle style, TextAnchor alignment)
        {
            var label = new GameObject(value, typeof(RectTransform), typeof(Text)).GetComponent<RectTransform>();
            label.SetParent(parent, false); label.anchorMin = new Vector2(0f, 1f); label.anchorMax = new Vector2(0f, 1f);
            label.pivot = new Vector2(0f, 1f); label.anchoredPosition = position; label.sizeDelta = size;
            var text = label.GetComponent<Text>();
            text.text = value; text.font = Resources.GetBuiltinResource<Font>("Arial.ttf"); text.fontSize = fontSize;
            text.color = color; text.fontStyle = style; text.alignment = alignment; text.horizontalOverflow = HorizontalWrapMode.Wrap;
            text.verticalOverflow = VerticalWrapMode.Overflow;
            return text;
        }

        private Button CreateButton(RectTransform parent, string label, Vector2 position, Vector2 size, Action onClick)
        {
            var buttonObject = new GameObject(label + " button", typeof(RectTransform), typeof(Image), typeof(Button));
            var rect = buttonObject.GetComponent<RectTransform>(); rect.SetParent(parent, false);
            rect.anchorMin = new Vector2(0f, 1f); rect.anchorMax = new Vector2(0f, 1f); rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = position; rect.sizeDelta = size;
            buttonObject.GetComponent<Image>().color = new Color(0.11f, 0.15f, 0.15f, 0.95f);
            var button = buttonObject.GetComponent<Button>();
            button.targetGraphic = buttonObject.GetComponent<Image>(); button.onClick.AddListener(() => onClick());
            var colors = button.colors; colors.highlightedColor = new Color(0.22f, 0.29f, 0.28f); colors.pressedColor = _accent; button.colors = colors;
            var text = CreateText(rect, label, new Vector2(10f, -3f), new Vector2(size.x - 20f, size.y - 6f), 12, new Color(0.88f, 0.91f, 0.88f), FontStyle.Bold, TextAnchor.MiddleCenter);
            text.rectTransform.anchorMin = Vector2.zero; text.rectTransform.anchorMax = Vector2.one; text.rectTransform.pivot = new Vector2(0.5f, 0.5f); text.rectTransform.anchoredPosition = Vector2.zero; text.rectTransform.sizeDelta = Vector2.zero;
            return button;
        }

        private InputField CreateInput(RectTransform parent, string placeholder, Vector2 position, Vector2 size, string initialValue)
        {
            var fieldObject = new GameObject("Input " + placeholder, typeof(RectTransform), typeof(Image), typeof(InputField));
            var rect = fieldObject.GetComponent<RectTransform>(); rect.SetParent(parent, false);
            rect.anchorMin = new Vector2(0f, 1f); rect.anchorMax = new Vector2(0f, 1f); rect.pivot = new Vector2(0f, 1f);
            rect.anchoredPosition = position; rect.sizeDelta = size; fieldObject.GetComponent<Image>().color = new Color(0.045f, 0.065f, 0.065f, 0.98f);
            var input = fieldObject.GetComponent<InputField>();
            var text = CreateText(rect, initialValue, new Vector2(10f, -4f), new Vector2(size.x - 20f, size.y - 8f), 11, Color.white, FontStyle.Normal, TextAnchor.MiddleLeft);
            text.rectTransform.anchorMin = Vector2.zero; text.rectTransform.anchorMax = Vector2.one; text.rectTransform.pivot = new Vector2(0.5f, 0.5f); text.rectTransform.anchoredPosition = Vector2.zero; text.rectTransform.sizeDelta = new Vector2(-20f, -8f);
            var hint = CreateText(rect, placeholder, new Vector2(10f, -4f), new Vector2(size.x - 20f, size.y - 8f), 10, new Color(0.52f, 0.60f, 0.57f), FontStyle.Italic, TextAnchor.MiddleLeft);
            hint.rectTransform.anchorMin = Vector2.zero; hint.rectTransform.anchorMax = Vector2.one; hint.rectTransform.pivot = new Vector2(0.5f, 0.5f); hint.rectTransform.anchoredPosition = Vector2.zero; hint.rectTransform.sizeDelta = new Vector2(-20f, -8f);
            input.textComponent = text; input.placeholder = hint; input.text = initialValue;
            return input;
        }

        private void OpenModule(string module)
        {
            _activeModule = module;
            _pageTitle.text = module;
            var index = Array.IndexOf(ModuleNames, module);
            _activeTabTarget = new Rect(index * 164f, 0f, 164f, 3f);
            DrawPage();
        }

        private void DrawPage()
        {
            foreach (Transform child in _pageBody) Destroy(child.gameObject);
            if (_activeModule == "Dashboard") DrawDashboard();
            else if (_activeModule == "Match History") DrawHistory();
            else if (_activeModule == "Operator Analytics") DrawOperators();
            else if (_activeModule == "Team Analytics") DrawTeams();
            else DrawSchools();
        }

        private void DrawDashboard()
        {
            var summary = string.IsNullOrEmpty(_summaryJson) ? null : JsonUtility.FromJson<SummaryEnvelope>(_summaryJson);
            var rounds = summary == null ? 0 : summary.rounds_logged;
            var players = summary == null || summary.players == null ? 0 : summary.players.Length;
            var teams = summary == null || summary.teams == null ? 0 : summary.teams.Length;
            CreateMetric("ROUNDS LOGGED", rounds.ToString(), new Vector2(0f, -3f));
            CreateMetric("TRACKED PLAYERS", players.ToString(), new Vector2(220f, -3f));
            CreateMetric("TEAMS", teams.ToString(), new Vector2(440f, -3f));
            CreateText(_pageBody, "LOCAL REPLAY SOURCE", new Vector2(0f, -124f), new Vector2(290f, 20f), 10, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
            _pathInput = CreateInput(_pageBody, "MatchReplay folder, match folder, .zip or .rec path", new Vector2(0f, -152f), new Vector2(540f, 40f), "");
            CreateButton(_pageBody, "ANALYZE REPLAY", new Vector2(555f, -152f), new Vector2(170f, 40f), () => StartCoroutine(ParseReplayPath()));
            if (_currentReplay != null && _currentReplay.match != null)
            {
                CreateText(_pageBody, "PARSED MATCH", new Vector2(0f, -222f), new Vector2(200f, 18f), 10, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
                CreateText(_pageBody, _currentReplay.match.map + "   /   " + _currentReplay.match.match_id,
                    new Vector2(0f, -250f), new Vector2(700f, 30f), 21, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
                var score = _currentReplay.match.final_score ?? new int[2];
                CreateText(_pageBody, score[0] + " : " + score[1] + "     " + (_currentReplay.match.players == null ? 0 : _currentReplay.match.players.Length) + " PLAYERS     PICK THE PLAYERS TO TRACK",
                    new Vector2(0f, -284f), new Vector2(520f, 22f), 12, new Color(0.65f, 0.76f, 0.71f), FontStyle.Normal, TextAnchor.MiddleLeft);
                CreateButton(_pageBody, "SAVE " + _pickedPlayers.Count + " TO SEASON", new Vector2(578f, -278f), new Vector2(180f, 34f), () => StartCoroutine(SaveCurrentMatch()));
                DrawPlayerPicks(_currentReplay.match);
            }
            else
            {
                CreateText(_pageBody, "Replay files are validated before parsing. Match data is stored only when you choose Save to Season.",
                    new Vector2(0f, -226f), new Vector2(690f, 46f), 12, new Color(0.63f, 0.71f, 0.68f), FontStyle.Normal, TextAnchor.MiddleLeft);
            }
            CreateText(_pageBody, "SEASON", new Vector2(0f, -385f), new Vector2(70f, 18f), 9, new Color(0.56f, 0.64f, 0.60f), FontStyle.Bold, TextAnchor.MiddleLeft);
            CreateInput(_pageBody, "Season label", new Vector2(78f, -379f), new Vector2(180f, 33f), _season).onEndEdit.AddListener(value => _season = string.IsNullOrWhiteSpace(value) ? "current" : value.Trim());
            _apiInput = CreateInput(_pageBody, "Local or hosted API URL", new Vector2(285f, -379f), new Vector2(350f, 33f), _apiBase);
            CreateButton(_pageBody, "CONNECT", new Vector2(646f, -379f), new Vector2(112f, 33f), ConnectApi);
        }

        private void DrawPlayerPicks(NormalizedMatch match)
        {
            if (match.players == null) return;
            for (var teamIndex = 0; teamIndex < 2; teamIndex++)
            {
                var x = 0f;
                foreach (var player in match.players.Where(item => item.team == teamIndex).Take(5))
                {
                    var name = player.name;
                    var label = (_pickedPlayers.Contains(name) ? "[X] " : "[  ] ") + name;
                    CreateButton(_pageBody, label, new Vector2(x, -314f - teamIndex * 30f), new Vector2(148f, 26f), () =>
                    {
                        if (!_pickedPlayers.Remove(name)) _pickedPlayers.Add(name);
                        DrawPage();
                    });
                    x += 152f;
                }
            }
        }

        private void CreateMetric(string label, string value, Vector2 position)
        {
            var tile = CreatePanel(_pageBody, label, new Vector2(0f, 1f), new Vector2(0f, 1f), new Vector2(0f, 1f), position, new Vector2(205f, 98f), new Color(0.11f, 0.15f, 0.15f, 0.96f));
            CreateText(tile, label, new Vector2(12f, -11f), new Vector2(180f, 18f), 9, new Color(0.60f, 0.68f, 0.64f), FontStyle.Bold, TextAnchor.MiddleLeft);
            CreateText(tile, value, new Vector2(12f, -38f), new Vector2(180f, 43f), 25, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
        }

        private void DrawHistory()
        {
            CreateText(_pageBody, "Saved replay sessions", new Vector2(0f, -3f), new Vector2(600f, 25f), 16, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
            var history = string.IsNullOrEmpty(_historyJson) ? null : JsonUtility.FromJson<HistoryEnvelope>(_historyJson);
            if (history == null || history.matches == null || history.matches.Length == 0)
                CreateText(_pageBody, "No saved match history is available yet.", new Vector2(0f, -45f), new Vector2(650f, 25f), 12, new Color(0.63f, 0.71f, 0.68f), FontStyle.Normal, TextAnchor.MiddleLeft);
            else
            {
                var y = -43f;
                foreach (var match in history.matches.Take(9))
                {
                    var line = match.match_id + "     " + match.rounds + " ROUNDS     " + match.players + " PLAYERS     " + match.saved_at;
                    CreateText(_pageBody, line, new Vector2(0f, y), new Vector2(780f, 28f), 11, new Color(0.82f, 0.87f, 0.83f), FontStyle.Normal, TextAnchor.MiddleLeft);
                    y -= 34f;
                }
            }
        }

        private void DrawOperators()
        {
            CreateText(_pageBody, "Operator selections are analyzed from the replay currently loaded on Dashboard.", new Vector2(0f, -3f), new Vector2(760f, 30f), 12, new Color(0.72f, 0.80f, 0.76f), FontStyle.Normal, TextAnchor.MiddleLeft);
            if (_currentReplay == null) CreateText(_pageBody, "No replay loaded in this session.", new Vector2(0f, -46f), new Vector2(600f, 25f), 12, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
            else
            {
                CreateText(_pageBody, _currentReplay.match.map + "  /  " + _currentReplay.match_id, new Vector2(0f, -46f), new Vector2(700f, 25f), 16, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
                var picks = new Dictionary<string, int>();
                foreach (var player in _currentReplay.match.players ?? Array.Empty<MatchPlayer>())
                    foreach (var operatorName in player.operator_history ?? Array.Empty<string>())
                        if (!string.IsNullOrWhiteSpace(operatorName)) picks[operatorName] = picks.TryGetValue(operatorName, out var count) ? count + 1 : 1;
                var y = -88f;
                foreach (var pick in picks.OrderByDescending(item => item.Value).Take(12))
                {
                    CreateText(_pageBody, pick.Key + "     /     " + pick.Value + " ROUND PICKS", new Vector2(0f, y), new Vector2(500f, 27f), 13, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
                    y -= 33f;
                }
                if (picks.Count == 0) CreateText(_pageBody, "This replay did not include operator selections.", new Vector2(0f, -88f), new Vector2(600f, 25f), 12, new Color(0.63f, 0.71f, 0.68f), FontStyle.Normal, TextAnchor.MiddleLeft);
            }
        }

        private void DrawTeams()
        {
            var summary = string.IsNullOrEmpty(_summaryJson) ? null : JsonUtility.FromJson<SummaryEnvelope>(_summaryJson);
            if (summary == null || summary.teams == null || summary.teams.Length == 0)
            {
                CreateText(_pageBody, "No team totals yet. Track a school roster and save replay matches.", new Vector2(0f, -3f), new Vector2(760f, 30f), 12, new Color(0.72f, 0.80f, 0.76f), FontStyle.Normal, TextAnchor.MiddleLeft);
                return;
            }
            var y = -4f;
            foreach (var team in summary.teams)
            {
                var line = team.team + "     " + (team.players == null ? 0 : team.players.Length) + " PLAYERS     EPS " + (team.eps > 0 ? team.eps.ToString() : "—") + "     K/D " + team.kd.ToString("0.00") + "     KOST " + team.kost_avg.ToString("0.0") + "%";
                CreateText(_pageBody, line, new Vector2(0f, y), new Vector2(780f, 28f), 14, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
                y -= 42f;
            }
        }

        private void DrawSchools()
        {
            CreateText(_pageBody, "NECC R6 school directory", new Vector2(0f, -3f), new Vector2(600f, 26f), 16, Color.white, FontStyle.Bold, TextAnchor.MiddleLeft);
            CreateButton(_pageBody, "SYNC SCHOOL CATALOG", new Vector2(0f, -43f), new Vector2(205f, 38f), () => StartCoroutine(LoadSchools()));
            var catalog = string.IsNullOrEmpty(_schoolsJson) ? null : JsonUtility.FromJson<SchoolEnvelope>(_schoolsJson);
            if (catalog == null || catalog.schools == null || catalog.schools.Length == 0)
            {
                CreateText(_pageBody, "Configure NECC_R6_DATA_URL or import an authorized JSON catalog through the web client.",
                    new Vector2(0f, -97f), new Vector2(740f, 44f), 11, new Color(0.68f, 0.77f, 0.72f), FontStyle.Normal, TextAnchor.MiddleLeft);
                return;
            }
            var y = -97f;
            foreach (var school in catalog.schools.Take(7))
            {
                var current = school;
                CreateButton(_pageBody, school.name, new Vector2(0f, y), new Vector2(245f, 35f), () => SelectSchool(current));
                y -= 43f;
            }
            var selected = catalog.schools.FirstOrDefault(school => school.name == _selectedSchoolName);
            if (selected != null)
            {
                CreateText(_pageBody, selected.name, new Vector2(282f, -97f), new Vector2(420f, 30f), 22, _accent, FontStyle.Bold, TextAnchor.MiddleLeft);
                var team = selected.teams == null ? null : selected.teams.FirstOrDefault(item => item.name == _selectedTeamName) ?? selected.teams.FirstOrDefault();
                if (team != null)
                {
                    _selectedTeamName = team.name;
                    CreateText(_pageBody, team.name + "   /   " + (team.roster == null ? 0 : team.roster.Length) + " PLAYERS",
                        new Vector2(282f, -132f), new Vector2(420f, 24f), 12, new Color(0.7f, 0.79f, 0.74f), FontStyle.Normal, TextAnchor.MiddleLeft);
                    if (team.roster != null)
                    {
                        var rosterText = string.Join("\n", team.roster.Take(10));
                        CreateText(_pageBody, rosterText, new Vector2(282f, -168f), new Vector2(420f, 190f), 12, Color.white, FontStyle.Normal, TextAnchor.UpperLeft);
                        CreateButton(_pageBody, "TRACK ROSTER", new Vector2(282f, -375f), new Vector2(180f, 38f), () => StartCoroutine(TrackRoster(team)));
                    }
                }
            }
        }

        private IEnumerator RefreshData()
        {
            yield return Get("/seasons/" + UnityWebRequest.EscapeURL(_season) + "/summary", value => _summaryJson = value);
            yield return Get("/seasons/" + UnityWebRequest.EscapeURL(_season) + "/matches", value => _historyJson = value);
            DrawPage();
        }

        private IEnumerator ParseReplayPath()
        {
            if (_pathInput == null || string.IsNullOrWhiteSpace(_pathInput.text)) { SetStatus("Enter a replay path first."); yield break; }
            SetStatus("Validating replay files...");
            var body = JsonUtility.ToJson(new ReplayPathBody { path = _pathInput.text.Trim() });
            yield return Post("/replays/parse-path", body, value =>
            {
                var response = JsonUtility.FromJson<ParseResponse>(value);
                _currentReplay = response != null && response.matches != null && response.matches.Length > 0 ? response.matches[0] : null;
                _pickedPlayers.Clear();
                SetStatus(_currentReplay == null ? "No replay match returned." : "Replay parsed / " + _currentReplay.match.map);
            });
            DrawPage();
        }

        private IEnumerator SaveCurrentMatch()
        {
            if (_currentReplay == null || _currentReplay.match == null) { SetStatus("Parse a replay first."); yield break; }
            if (_pickedPlayers.Count == 0) { SetStatus("Pick the players to track from this match first."); yield break; }
            var body = BuildLogPayload(_currentReplay);
            SetStatus("Saving tracked rounds...");
            yield return Post("/seasons/" + UnityWebRequest.EscapeURL(_season) + "/matches/log", body, value =>
            {
                var result = JsonUtility.FromJson<LogResult>(value);
                SetStatus("Saved " + result.rounds_logged + " player-rounds; " + result.rounds_skipped_duplicate + " duplicates skipped.");
            });
            yield return RefreshData(); DrawPage();
        }

        private IEnumerator LoadSchools()
        {
            SetStatus("Loading school catalog...");
            yield return Get("/schools", value => { _schoolsJson = value; SetStatus("School catalog synced."); });
            DrawPage();
        }

        private void ConnectApi()
        {
            var value = _apiInput == null ? "" : _apiInput.text.Trim().TrimEnd('/');
            if (!Uri.TryCreate(value, UriKind.Absolute, out var endpoint) || (endpoint.Scheme != "http" && endpoint.Scheme != "https"))
            {
                SetStatus("Enter a valid HTTP or HTTPS API base URL.");
                return;
            }
            _apiBase = value;
            PlayerPrefs.SetString("r6_api_base", _apiBase);
            StartCoroutine(RefreshData());
        }

        private void SelectSchool(SchoolData school)
        {
            _selectedSchoolName = school.name;
            _selectedTeamName = school.teams != null && school.teams.Length > 0 ? school.teams[0].name : "";
            if (ColorUtility.TryParseHtmlString(school.primary_color, out var schoolColor))
            {
                _accent = schoolColor;
                foreach (var renderer in _brandMeshes) if (renderer != null) renderer.material.color = _accent;
                foreach (var light in FindObjectsOfType<Light>()) if (light.name == "Warm key light") light.color = _accent;
            }
            DrawPage();
        }

        private IEnumerator TrackRoster(SchoolTeamData team)
        {
            if (team.roster == null || team.roster.Length == 0) { SetStatus("This school team has no published roster."); yield break; }
            var body = JsonUtility.ToJson(new RosterBody { team = team.name, players = team.roster });
            yield return Post("/seasons/" + UnityWebRequest.EscapeURL(_season) + "/rosters", body,
                value => SetStatus(team.roster.Length + " players added to " + team.name + "."));
            yield return RefreshData();
        }

        private string BuildLogPayload(ParsedReplay replay)
        {
            var json = new StringBuilder("{\"match\":");
            json.Append(replay.match_json).Append(",\"rosters\":{");
            var wroteTeam = false;
            var match = replay.match;
            if (match.players != null)
            {
                var teamCount = match.team_names == null ? 0 : match.team_names.Length;
                for (var teamIndex = 0; teamIndex < teamCount; teamIndex++)
                {
                    var roster = match.players.Where(player => player.team == teamIndex && _pickedPlayers.Contains(player.name)).Select(player => player.name).ToArray();
                    if (roster.Length == 0) continue;
                    if (wroteTeam) json.Append(',');
                    json.Append(QuoteJson(match.team_names[teamIndex])).Append(':').Append(ToJsonArray(roster));
                    wroteTeam = true;
                }
            }
            return json.Append("}}").ToString();
        }

        private static string ToJsonArray(IEnumerable<string> values)
        {
            return "[" + string.Join(",", values.Select(QuoteJson)) + "]";
        }

        private static string QuoteJson(string value)
        {
            var wrapped = JsonUtility.ToJson(new StringWrapper { value = value });
            return wrapped.Substring(9, wrapped.Length - 10);
        }

        private IEnumerator Get(string path, Action<string> onSuccess)
        {
            using (var request = UnityWebRequest.Get(_apiBase + path))
            {
                request.timeout = 20;
                yield return request.SendWebRequest();
                if (request.result == UnityWebRequest.Result.Success)
                {
                    SetStatus("API LINK / " + _apiBase);
                    onSuccess(request.downloadHandler.text);
                }
                else SetStatus("API error: " + request.error);
            }
        }

        private IEnumerator Post(string path, string body, Action<string> onSuccess)
        {
            using (var request = new UnityWebRequest(_apiBase + path, "POST"))
            {
                request.uploadHandler = new UploadHandlerRaw(Encoding.UTF8.GetBytes(body));
                request.downloadHandler = new DownloadHandlerBuffer();
                request.SetRequestHeader("Content-Type", "application/json");
                request.timeout = 180;
                yield return request.SendWebRequest();
                if (request.result == UnityWebRequest.Result.Success) onSuccess(request.downloadHandler.text);
                else SetStatus("API error: " + request.error + " / " + request.downloadHandler.text);
            }
        }

        private void SetStatus(string message)
        {
            if (_statusText != null) _statusText.text = message;
        }

        [Serializable] private sealed class ReplayPathBody { public string path; }
        [Serializable] private sealed class ParseResponse { public ParsedReplay[] matches; }
        [Serializable] private sealed class ParsedReplay { public string name; public string match_json; public string[] warnings; public NormalizedMatch match; }
        [Serializable] private sealed class NormalizedMatch { public string map; public string match_id; public string[] team_names; public int[] final_score; public MatchPlayer[] players; }
        [Serializable] private sealed class MatchPlayer { public string name; public int team; public string[] operator_history; }
        [Serializable] private sealed class StringWrapper { public string value; }
        [Serializable] private sealed class LogResult { public int rounds_logged; public int rounds_skipped_duplicate; }
        [Serializable] private sealed class HistoryEnvelope { public HistoryRow[] matches; }
        [Serializable] private sealed class HistoryRow { public string match_id; public string saved_at; public int rounds; public int players; }
        [Serializable] private sealed class RosterBody { public string team; public string[] players; }
        [Serializable] private sealed class SchoolEnvelope { public SchoolData[] schools; public string source; }
        [Serializable] private sealed class SchoolData { public string name; public string primary_color; public string logo_url; public SchoolTeamData[] teams; }
        [Serializable] private sealed class SchoolTeamData { public string name; public string[] roster; }
        [Serializable] private sealed class SummaryEnvelope { public int rounds_logged; public PlayerSummary[] players; public TeamSummary[] teams; }
        [Serializable] private sealed class PlayerSummary { public string username; public float kd; public float kost_pct; }
        [Serializable] private sealed class TeamSummary { public string team; public string[] players; public float kd; public float kost_avg; public int eps; }
    }
}