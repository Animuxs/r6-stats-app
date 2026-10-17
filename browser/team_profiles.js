/* Portable profiles; authentication belongs to the online team server. */
(() => {
  "use strict";
  const roles = ["Player", "Captain", "Coach", "Analyst", "Staff", "Other"];
  function directory(value = []) {
    if (!Array.isArray(value) || value.length > 100) throw Error("Use at most 100 member profiles.");
    const ids = new Set(), players = new Set();
    return value.map(person => {
      if (!person || typeof person !== "object") throw Error("Invalid member profile.");
      const row = {};
      for (const key of ["id", "name", "player", "custom_role"]) {
        if (person[key] != null && typeof person[key] !== "string") throw Error("Invalid member field.");
        row[key] = (person[key] || "").trim();
      }
      if (!/^[a-f0-9]{32}$/.test(row.id) || ids.has(row.id)) throw Error("Members need unique profile IDs.");
      if (!row.name || [row.name, row.player, row.custom_role].some(v => v.length > 100)) throw Error("Use names and titles of 1–100 characters.");
      if (!Array.isArray(person.roles) || !person.roles.length || person.roles.some(r => !roles.includes(r))) throw Error("Choose at least one recognized role.");
      row.roles = [...new Set(person.roles)];
      if (row.roles.includes("Other") && !row.custom_role) throw Error("Name the custom role when choosing Other.");
      const player = row.player.toLowerCase();
      if (player && players.has(player)) throw Error("Each player username can belong to only one member.");
      ids.add(row.id); if (player) players.add(player);
      return row;
    });
  }
  function serverUrl(value) {
    let url;
    try { url = new URL(value); } catch { throw Error("Enter the full team server URL."); }
    const local = ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname);
    if ((url.protocol !== "https:" && !(local && url.protocol === "http:")) || url.username || url.password)
      throw Error("Use HTTPS for a team server, or HTTP on localhost. Do not put credentials in the URL.");
    if (url.search || url.hash) throw Error("Enter the server address without query parameters or a fragment.");
    return url.href;
  }
  const api = {roles, directory, serverUrl};
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else window.R6Profiles = api;
})();
