package dissect

import (
	"bytes"
	"encoding/binary"

	"github.com/rs/zerolog/log"
)

// Newer replays (seen on Y11S3) no longer put the player ID after a scoreboard
// value. Instead, each player has a scoreboard entity ID, linked to the player
// by a reference right before their player block. Updates are written as
//
//	<entity id> 00000000 <field> 04 <uint32 value>
//
// and the round-start snapshot writes every field for an entity at once:
//
//	<entity id> 00000000 <kills> 04 <value> 22 <deaths> 04 <value> 22 <assists> 04 <value> ...
var scoreboardEntityIndicator = []byte{0xEB, 0x21, 0x9B, 0x38}
var scoreboardKillsField = []byte{0x1C, 0xD2, 0xB1, 0x9D}
var scoreboardDeathsField = []byte{0xCD, 0x9C, 0x5D, 0x72}

const scoreboardFieldSize = 10 // field (4) + 0x04 (1) + value (4) + 0x22 (1)

type scoreboardEntity struct {
	username string
	kills    uint32
	assists  uint32
	// assists at the start of the round, from the snapshot
	assistsBase uint32
}

type Scoreboard struct {
	Players []ScoreboardPlayer
}

type ScoreboardPlayer struct {
	ID               []byte
	Score            uint32
	Assists          uint32
	AssistsFromRound uint32
}

// readScoreboardEntity links a scoreboard entity ID to the player block that follows it.
func readScoreboardEntity(r *Reader) error {
	id, err := r.Bytes(4)
	if err != nil {
		return err
	}
	end := r.offset + 256
	if end > len(r.b) {
		end = len(r.b)
	}
	i := bytes.Index(r.b[r.offset:end], playerIndicator)
	if i == -1 {
		return nil
	}
	r.offset += i + len(playerIndicator)
	username, err := r.String()
	if err != nil {
		return err
	}
	if r.scoreboardEntities == nil {
		r.scoreboardEntities = make(map[string]*scoreboardEntity)
	}
	r.scoreboardEntities[string(id)] = &scoreboardEntity{username: username}
	log.Debug().Hex("id", id).Str("username", username).Msg("scoreboard_entity")
	return nil
}

// scoreboardEntityAt returns the entity owning the scoreboard field that starts at start,
// and whether the field is part of the round-start snapshot rather than an update.
func (r *Reader) scoreboardEntityAt(start int) (e *scoreboardEntity, snapshot bool) {
	if len(r.scoreboardEntities) == 0 {
		return nil, false
	}
	zero := []byte{0x00, 0x00, 0x00, 0x00}
	// walk back through snapshot fields to the entity id
	for k := start; k >= 8; k -= scoreboardFieldSize {
		if bytes.Equal(r.b[k-4:k], zero) {
			e, ok := r.scoreboardEntities[string(r.b[k-8:k-4])]
			if !ok {
				return nil, false
			}
			if k == start {
				// the snapshot's first field (kills) looks like an update, but is followed by more fields
				next := start + scoreboardFieldSize
				snapshot = next+4 <= len(r.b) && r.b[next-1] == 0x22 && bytes.Equal(r.b[next:next+4], scoreboardDeathsField)
			} else {
				snapshot = true
			}
			return e, snapshot
		}
		if k < scoreboardFieldSize || r.b[k-1] != 0x22 || r.b[k-6] != 0x04 {
			return nil, false
		}
	}
	return nil, false
}

// scoreboardValue reads the uint32 of the field whose 4-byte pattern ends at r.offset.
func (r *Reader) scoreboardValue() (uint32, bool) {
	i := r.offset
	if i+5 > len(r.b) || r.b[i] != 0x04 {
		return 0, false
	}
	return binary.LittleEndian.Uint32(r.b[i+1 : i+5]), true
}

func readScoreboardKills(r *Reader) error {
	if e, snapshot := r.scoreboardEntityAt(r.offset - 4); e != nil {
		kills, ok := r.scoreboardValue()
		if !ok {
			return nil
		}
		if !snapshot {
			for n := e.kills; n < kills; n++ {
				r.scoreboardKills = append(r.scoreboardKills, e.username)
			}
			log.Debug().Str("username", e.username).Uint32("kills", kills).Msg("scoreboard_kill")
		}
		e.kills = kills
		return nil
	}
	kills, err := r.Uint32()
	if err != nil {
		return err
	}
	if err := r.Skip(30); err != nil {
		return err
	}
	id, err := r.Bytes(4)
	if err != nil {
		return err
	}
	idx := r.PlayerIndexByID(id)
	if idx != -1 {
		username := r.Header.Players[idx].Username
		log.Debug().
			Str("username", username).
			Uint32("kills", kills).
			Msg("scoreboard_kill")
	}
	return nil
}

func readScoreboardAssists(r *Reader) error {
	if e, snapshot := r.scoreboardEntityAt(r.offset - 4); e != nil {
		assists, ok := r.scoreboardValue()
		if !ok {
			return nil
		}
		if snapshot {
			e.assistsBase = assists
		}
		e.assists = assists
		if idx := r.PlayerIndexByUsername(e.username); idx != -1 && idx < len(r.Scoreboard.Players) {
			r.Scoreboard.Players[idx].Assists = assists
			r.Scoreboard.Players[idx].AssistsFromRound = assists - e.assistsBase
		}
		log.Debug().Uint32("assists", assists).Str("username", e.username).Msg("scoreboard_assists")
		return nil
	}
	assists, err := r.Uint32()
	if err != nil {
		return err
	}
	if assists == 0 {
		return nil
	}
	if err = r.Skip(30); err != nil {
		return err
	}
	id, err := r.Bytes(4)
	if err != nil {
		return err
	}
	idx := r.PlayerIndexByID(id)
	username := "N/A"
	if idx != -1 {
		username = r.Header.Players[idx].Username
		if idx < len(r.Scoreboard.Players) {
			r.Scoreboard.Players[idx].Assists = assists
			r.Scoreboard.Players[idx].AssistsFromRound++
		}
	}
	log.Debug().
		Uint32("assists", assists).
		Str("username", username).
		Msg("scoreboard_assists")
	return nil
}

func readScoreboardScore(r *Reader) error {
	if e, _ := r.scoreboardEntityAt(r.offset - 4); e != nil {
		score, ok := r.scoreboardValue()
		if !ok {
			return nil
		}
		if idx := r.PlayerIndexByUsername(e.username); idx != -1 && idx < len(r.Scoreboard.Players) {
			r.Scoreboard.Players[idx].Score = score
		}
		log.Debug().Uint32("score", score).Str("username", e.username).Msg("scoreboard_score")
		return nil
	}
	score, err := r.Uint32()
	if err != nil {
		return err
	}
	if score == 0 {
		return nil
	}
	if err = r.Skip(13); err != nil {
		return err
	}
	id, err := r.Bytes(4)
	if err != nil {
		return err
	}
	idx := r.PlayerIndexByID(id)
	username := "N/A"
	if idx != -1 {
		username = r.Header.Players[idx].Username
		if idx < len(r.Scoreboard.Players) {
			r.Scoreboard.Players[idx].Score = score
		}
	}
	log.Debug().
		Uint32("score", score).
		Str("username", username).
		Msg("scoreboard_score")
	return nil
}

// fixKillersFromScoreboard corrects kill feed entries that credit the wrong player.
// The feed names whoever finished the target, while the scoreboard credits whoever downed them.
// Scoreboard updates are not always written before their feed entry, so the feed killers are
// aligned with the scoreboard kill increments (longest common subsequence), and a feed kill is
// only reassigned when it is unmatched and an unmatched scoreboard kill falls in the same gap.
func (r *Reader) fixKillersFromScoreboard() {
	if len(r.scoreboardKills) == 0 {
		return
	}
	kills := make([]int, 0)
	for i, u := range r.MatchFeedback {
		if u.Type == Kill {
			kills = append(kills, i)
		}
	}
	sb := r.scoreboardKills
	n, m := len(kills), len(sb)
	feed := func(i int) string { return r.MatchFeedback[kills[i]].Username }
	lcs := make([][]int, n+1)
	for i := range lcs {
		lcs[i] = make([]int, m+1)
	}
	for i := n - 1; i >= 0; i-- {
		for j := m - 1; j >= 0; j-- {
			if feed(i) == sb[j] {
				lcs[i][j] = lcs[i+1][j+1] + 1
			} else {
				lcs[i][j] = max(lcs[i+1][j], lcs[i][j+1])
			}
		}
	}
	var gapFeed, gapSB []int
	flush := func() {
		for k := 0; k < len(gapFeed) && k < len(gapSB); k++ {
			r.reassignKiller(kills[gapFeed[k]], sb[gapSB[k]])
		}
		gapFeed, gapSB = nil, nil
	}
	for i, j := 0, 0; i < n || j < m; {
		if i < n && j < m && feed(i) == sb[j] {
			flush()
			i++
			j++
		} else if j >= m || (i < n && lcs[i+1][j] >= lcs[i][j+1]) {
			gapFeed = append(gapFeed, i)
			i++
		} else {
			gapSB = append(gapSB, j)
			j++
		}
	}
	flush()
}

func (r *Reader) reassignKiller(feedIndex int, username string) {
	u := &r.MatchFeedback[feedIndex]
	killer := r.PlayerIndexByUsername(username)
	target := r.PlayerIndexByUsername(u.Target)
	if killer == -1 || target == -1 || r.Header.Players[killer].TeamIndex == r.Header.Players[target].TeamIndex {
		return
	}
	log.Debug().Str("feed", u.Username).Str("scoreboard", username).Str("target", u.Target).Msg("kill credited from scoreboard")
	u.Username = username
}
