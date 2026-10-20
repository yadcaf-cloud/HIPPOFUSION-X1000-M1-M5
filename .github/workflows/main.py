#!/usr/bin/env python3
"""HIPPOFUSION X1000 M1-M5: analyse prudente, FIGE immuable et audit.

Usage:
 python main.py --diagnostic
 python main.py --date 2026-10-21
 python main.py --date 2026-10-21 --figer
 python main.py --date 2026-10-21 --input courses.json --figer

Format JSON accepté: {"courses":[{"id":"R1C1","hippodrome":"...",
"depart":"2026-10-21T13:00:00+02:00","partants":[{"numero":1,
"nom":"...","musique":"1p2p3p","cote":5.2,"age":5,
"corde":2,"poids":57,"non_partant":false}],"arrivee":[1,2,3,4,5],
"resultat_officiel":true}]}
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo('Europe/Paris')
VERSION = '3.0.0'
DATA = Path(os.getenv('HIPPO_DATA_DIR', 'data'))
USER_AGENT = 'HIPPOFUSION-X1000/3.0 (+public data validation)'


def now():
    return dt.datetime.now(PARIS)


def stamp():
    return now().isoformat(timespec='seconds')


def save(path, obj, immutable=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    blob = (json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + '\n').encode()
    if immutable:
        try:
            with path.open('xb') as f:
                f.write(blob)
            return True
        except FileExistsError:
            return False
    with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as f:
        f.write(blob)
        temp = Path(f.name)
    os.replace(temp, path)
    return True


def load(path, default=None):
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return default


def digest(obj):
    raw = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    return hashlib.sha256(raw).hexdigest()


def fetch(url):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/json'})
    with urllib.request.urlopen(req, timeout=12) as resp:
        if resp.status != 200:
            raise ValueError('HTTP ' + str(resp.status))
        return json.load(resp)


def as_int(v):
    try:
        if isinstance(v, bool):
            return None
        return int(v)
    except (ValueError, TypeError, OverflowError):
        return None


def as_float(v):
    try:
        x = float(str(v).replace(',', '.'))
        return x if math.isfinite(x) else None
    except (ValueError, TypeError, OverflowError):
        return None


def parse_time(s):
    if not isinstance(s, str):
        return None
    try:
        t = dt.datetime.fromisoformat(s.replace('Z', '+00:00'))
        if t.tzinfo is None:
            return None
        return t.astimezone(PARIS)
    except ValueError:
        return None


def pick(obj, *names, default=None):
    for key in names:
        if isinstance(obj, dict) and obj.get(key) is not None:
            return obj[key]
    return default


def normalize(raw):
    """Adaptateur conservateur: refuse toute information de départ incertaine."""
    if not isinstance(raw, dict):
        return None
    race_id = str(pick(raw, 'id', 'course_id', default='')).strip()
    start = parse_time(pick(raw, 'depart', 'start', 'heure_depart'))
    horses = pick(raw, 'partants', 'participants', 'horses', default=[])
    if not race_id or start is None or not isinstance(horses, list):
        return None
    clean, used = [], set()
    for h in horses:
        if not isinstance(h, dict):
            continue
        n = as_int(pick(h, 'numero', 'num', 'number'))
        name = str(pick(h, 'nom', 'name', default='')).strip()
        if n is None or n <= 0 or n in used or not name:
            return None
        used.add(n)
        clean.append({'numero': n, 'nom': name,
                      'non_partant': bool(pick(h, 'non_partant', 'nonPartant', default=False)),
                      'musique': str(pick(h, 'musique', 'form', default='')),
                      'cote': as_float(pick(h, 'cote', 'odds')),
                      'age': as_int(pick(h, 'age')),
                      'corde': as_int(pick(h, 'corde', 'draw')),
                      'poids': as_float(pick(h, 'poids', 'weight'))})
    active = [h for h in clean if not h['non_partant']]
    if len(active) < 5:
        return None
    arrival = pick(raw, 'arrivee', 'arrival', default=[])
    if not isinstance(arrival, list):
        arrival = []
    arrival = [as_int(x) for x in arrival]
    arrival = [x for x in arrival if x is not None]
    official = pick(raw, 'resultat_officiel', 'official_result', default=False) is True
    if len(arrival) != len(set(arrival)) or any(x not in {h['numero'] for h in active} for x in arrival):
        arrival, official = [], False
    return {'id': race_id, 'hippodrome': str(pick(raw, 'hippodrome', 'track', default='Inconnu')),
            'depart': start.isoformat(), 'partants': clean, 'arrivee': arrival,
            'resultat_officiel': official, 'source': str(pick(raw, 'source', default='fichier'))}


def form_score(music):
    """Forme générique: positions récentes; aucune interprétation d'obstacle/trot inventée."""
    vals = [int(x) for x in re.findall(r'(?<!\d)([1-9])(?!\d)', music)[:5]]
    if not vals:
        return None
    weights = [1.0, .8, .65, .5, .4]
    return sum(max(0, 11 - x) / 10 * weights[i] for i, x in enumerate(vals)) / sum(weights[:len(vals)])


def metrics(h, field):
    fs = form_score(h['musique'])
    odds = h['cote']
    odds_signal = (1 / (1 + odds / 8)) if odds is not None and odds > 1 else None
    ages = [x['age'] for x in field if x['age'] is not None]
    age_signal = (1 / (1 + abs(h['age'] - 5) / 6)) if h['age'] is not None else None
    draws = [x['corde'] for x in field if x['corde'] is not None]
    # La corde ne reçoit pas de préférence sans informations de piste/distance.
    draw_signal = None
    weights = [x['poids'] for x in field if x['poids'] is not None]
    weight_signal = (max(0, min(1, .5 + (sum(weights) / len(weights) - h['poids']) / 20))
                     if weights and h['poids'] is not None else None)
    return {'forme': fs, 'marche': odds_signal, 'age': age_signal,
            'poids': weight_signal, 'corde': draw_signal}


ENGINES = {
    'M1': {'forme': .65, 'marche': .35},
    'M2': {'forme': .35, 'marche': .65},
    'M3': {'forme': .60, 'marche': .25, 'poids': .15},
    'M4': {'forme': .75, 'marche': .15, 'age': .10},
    'M5': {'forme': .45, 'marche': .40, 'poids': .10, 'age': .05},
}


def analyze(race):
    field = [h for h in race['partants'] if not h['non_partant']]
    signals = {h['numero']: metrics(h, field) for h in field}
    results = {}
    for engine, weights in ENGINES.items():
        scored = []
        for h in field:
            values = signals[h['numero']]
            available = [(w, values[k]) for k, w in weights.items() if values[k] is not None]
            coverage = sum(w for w, _ in available)
            score = sum(w * v for w, v in available) / coverage if coverage else None
            if score is not None:
                scored.append((score, coverage, h['numero'], h['nom']))
        scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
        # Au moins deux signaux fiables par moteur et couverture suffisante.
        eligible = [x for x in scored if x[1] >= .65 and
                    sum(signals[x[2]][k] is not None for k in weights) >= 2]
        if len(eligible) < 5:
            results[engine] = {'statut': 'ABSTENTION', 'raison': 'Données insuffisantes pour 5 chevaux'}
            continue
        top = eligible[:5]
        results[engine] = {
            'statut': 'PROVISOIRE', 'gagnant_espere': top[0][2],
            'place_espere': top[1][2], 'ordre_espere': [x[2] for x in top],
            'pari_retenu': 'Simple gagnant ' + str(top[0][2]),
            'scores': [{'numero': x[2], 'nom': x[3], 'score': round(x[0], 4),
                        'couverture': round(x[1], 3)} for x in top],
            'qualification': 'ETUDE', 'validation': 'Non vérifiée'}
    return results


def acquire(day, input_file):
    if input_file:
        obj = load(Path(input_file))
        if obj is None:
            raise RuntimeError('Fichier JSON introuvable ou invalide: ' + input_file)
        raw = obj.get('courses', []) if isinstance(obj, dict) else obj
        if not isinstance(raw, list):
            raise RuntimeError('Format attendu: {"courses": [...]}')
        return raw, 'fichier:' + input_file
    # Endpoint configurable: doit retourner {"courses": [...]} au format documenté.
    # Pas de fausse promesse de compatibilité avec une API PMU non documentée.
    endpoint = os.getenv('HIPPO_COURSES_URL', '').strip()
    if not endpoint:
        return [], 'Aucune source configurée (HIPPO_COURSES_URL ou --input)'
    if not endpoint.startswith('https://'):
        raise RuntimeError('HIPPO_COURSES_URL doit être en HTTPS')
    url = endpoint.replace('{date}', day)
    payload = fetch(url)
    raw = payload.get('courses', []) if isinstance(payload, dict) else payload
    if not isinstance(raw, list):
        raise RuntimeError('Réponse de collecte incompatible')
    return raw, url


def audit(race, locked):
    if not race['resultat_officiel'] or len(race['arrivee']) < 5:
        return {'statut': 'EN_ATTENTE_RESULTAT_OFFICIEL'}
    arrival = race['arrivee']
    output = {}
    for key, p in locked['moteurs'].items():
        if p['statut'] == 'ABSTENTION':
            output[key] = {'statut': 'ABSTENTION'}
            continue
        win = p['gagnant_espere'] == arrival[0]
        place = p['place_espere'] in arrival[:3]
        output[key] = {'gagnant_exact': win, 'place_top3': place,
                       'ordre_5_exact': p['ordre_espere'] == arrival[:5],
                       'pari': p['pari_retenu'], 'retour_financier': 'NON_CALCULE'}
    return {'statut': 'VERIFIE_SUR_RESULTAT_DECLARE_OFFICIEL', 'arrivee': arrival[:5],
            'moteurs': output, 'source_resultat': race['source']}


def run(args):
    date = dt.date.fromisoformat(args.date).isoformat()
    if args.diagnostic:
        print(json.dumps({'version': VERSION, 'python': sys.version.split()[0],
                          'date_paris': stamp(), 'data_dir': str(DATA),
                          'source_configuree': bool(args.input or os.getenv('HIPPO_COURSES_URL')),
                          'etat': 'PRET_POUR_CONTROLE'}, ensure_ascii=False, indent=2))
        return 0
    raw, source = acquire(date, args.input)
    races, rejected = [], 0
    for item in raw:
        race = normalize(item)
        if race and race['depart'][:10] == date:
            races.append(race)
        else:
            rejected += 1
    seen = set()
    races.sort(key=lambda r: (r['depart'], r['id']))
    unique = []
    for race in races:
        if race['id'] not in seen:
            unique.append(race)
            seen.add(race['id'])
        else:
            rejected += 1
    report = {'version': VERSION, 'date': date, 'execute_a': stamp(), 'source': source,
              'courses_recues': len(raw), 'courses_valides': len(unique),
              'courses_rejetees': rejected, 'courses': []}
    for race in unique:
        start = parse_time(race['depart'])
        seconds = (start - now()).total_seconds()
        lock_path = DATA / 'figes' / date / (re.sub(r'[^A-Za-z0-9_-]', '_', race['id']) + '.json')
        locked = load(lock_path)
        if locked is not None and (locked.get('course_id') != race['id'] or
                                   locked.get('integrite_sha256') != digest({k: v for k, v in locked.items() if k != 'integrite_sha256'})):
            raise RuntimeError('FIGE corrompu ou collision: ' + str(lock_path))
        if locked is None and seconds > 0:
            preds = analyze(race)
            status = 'PROVISOIRE'
            if args.figer and seconds >= args.marge_secondes and any(x['statut'] != 'ABSTENTION' for x in preds.values()):
                doc = {'course_id': race['id'], 'depart': race['depart'], 'fige_a': stamp(),
                       'source': race['source'], 'partants_sha256': digest(race['partants']),
                       'moteurs': preds}
                doc['integrite_sha256'] = digest(doc)
                if save(lock_path, doc, immutable=True):
                    locked = doc
                    status = 'FIGE'
                else:
                    locked = load(lock_path)
                    status = 'FIGE_EXISTANT'
            else:
                save(DATA / 'provisoires' / date / (re.sub(r'[^A-Za-z0-9_-]', '_', race['id']) + '.json'),
                     {'course': race['id'], 'calcule_a': stamp(), 'moteurs': preds})
        elif locked is not None:
            status = 'FIGE_EXISTANT'
            preds = locked['moteurs']
        else:
            status = 'DEPART_PASSE_SANS_FIGE'
            preds = {}
        entry = {'course': race['id'], 'hippodrome': race['hippodrome'],
                 'depart': race['depart'], 'statut': status,
                 'moteurs': preds if status != 'DEPART_PASSE_SANS_FIGE' else {},
                 'audit': audit(race, locked) if locked else {'statut': 'SANS_FIGE'}}
        report['courses'].append(entry)
    out = DATA / 'rapports' / (date + '.json')
    save(out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print('Rapport:', out, file=sys.stderr)
    if not raw:
        print('AVERTISSEMENT: aucune course collectée; aucun pronostic inventé.', file=sys.stderr)
    return 0


def main():
    p = argparse.ArgumentParser(description='HIPPOFUSION X1000 M1-M5')
    p.add_argument('--date', default=now().date().isoformat())
    p.add_argument('--input', help='Fichier JSON normalisé de courses et résultats')
    p.add_argument('--figer', action='store_true', help='Autoriser le FIGE avant départ')
    p.add_argument('--diagnostic', action='store_true')
    p.add_argument('--marge-secondes', type=int, default=60,
                   help='Marge minimale avant le départ pour FIGER (défaut 60)')
    args = p.parse_args()
    if args.marge_secondes < 1:
        p.error('--marge-secondes doit être >= 1')
    try:
        return run(args)
    except (ValueError, RuntimeError, OSError, urllib.error.URLError) as e:
        print('ERREUR:', str(e), file=sys.stderr)
        return 1


if __name__ == '__main__':
    sys.exit(main())
