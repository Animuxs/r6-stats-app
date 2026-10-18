package dissect

import "testing"

// A player who joins after the scoreboard's list was made (a round with 11 players for 10 scoreboard rows)
// used to crash PlayerStats with "index out of range [10] with length 10".
func TestPlayerStatsWithALatePlayer(t *testing.T) {
	r := &Reader{}
	r.Header.Teams[0].Won = true
	for i, name := range []string{"a0", "a1", "a2", "a3", "a4", "d0", "d1", "d2", "d3", "d4"} {
		r.Header.Players = append(r.Header.Players, Player{Username: name, TeamIndex: i / 5})
		r.Scoreboard.Players = append(r.Scoreboard.Players, ScoreboardPlayer{Score: uint32(100 + i)})
	}
	r.Header.Players = append(r.Header.Players, Player{Username: "late", TeamIndex: 1})
	stats := r.PlayerStats()
	if len(stats) != 11 {
		t.Fatalf("got %d players, want 11", len(stats))
	}
	if stats[10].Username != "late" || stats[10].Score != 0 {
		t.Errorf("late player: got %+v, want username late with no score", stats[10])
	}
	if stats[9].Score != 109 {
		t.Errorf("d4's score: got %d, want 109", stats[9].Score)
	}
}
