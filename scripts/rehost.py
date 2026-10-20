"""Explicitly combine chronological replay parts of one rehosted map."""
from copy import deepcopy


def combine(parts):
    if len(parts) < 2:
        raise ValueError('Select at least two replay parts in chronological order.')
    ids = [m.get('match_id') for m in parts]
    if not all(ids) or len(set(ids)) != len(ids):
        raise ValueError('Replay parts must have distinct match IDs.')
    if len({m.get('map') for m in parts}) != 1:
        raise ValueError('Rehost parts must be from the same map.')
    base = parts[0]
    reference = {p['name'].casefold(): p['team'] for p in base['players']}
    merged = deepcopy(parts[-1])
    merged['rounds'], players = [], {}
    for part in parts:
        side_map = {}
        for side in (0, 1):
            known = [reference[p['name'].casefold()] for p in part['players']
                     if p['team'] == side and p['name'].casefold() in reference]
            if not known or len(set(known)) != 1:
                raise ValueError('Cannot safely align the teams across these replay parts.')
            side_map[side] = known[0]
        if side_map[0] == side_map[1]:
            raise ValueError('Replay parts do not contain the same opposing teams.')
        for person in part['players']:
            p = deepcopy(person)
            p['team'] = side_map[p['team']]
            key = p['name'].casefold()
            if key not in players:
                players[key] = p
            else:
                players[key]['operator_history'] += p.get('operator_history', [])
        for rnd in part.get('rounds', []):
            r = deepcopy(rnd)
            if r.get('winner_team') not in (0, 1):
                raise ValueError('A round has no winner; the merged score cannot be verified.')
            r['winner_team'] = side_map[r['winner_team']]
            if r.get('attack_team') in (0, 1):
                r['attack_team'] = side_map[r['attack_team']]
            r['round_num'] = len(merged['rounds']) + 1
            merged['rounds'].append(r)
    merged['players'] = list(players.values())
    merged['team_names'] = deepcopy(base['team_names'])
    merged['final_score'] = [sum(r['winner_team'] == side for r in merged['rounds']) for side in (0, 1)]
    merged['played_at'] = base.get('played_at')
    return merged
