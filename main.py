#!/usr/bin/env python3
"""HIPPOFUSION X1000 M1–M5 — analyses traçables et FIGÉS protégés.

Entrée : JSON {"courses": [...]} dans data/courses.json par défaut.
Aucune collecte ni mise réelle de pari n'est effectuée.
"""
import argparse
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo('Europe/Paris')
FEATURES = ('forme', 'aptitude', 'regularite', 'jockey', 'entraineur', 'distance', 'terrain')
WEIGHTS = {
    'M1': (4, 3, 3, 1, 1, 2, 2),
    'M2': (2, 4, 2, 2, 1, 3, 3),
    'M3': (3, 2, 4, 2, 2, 1, 1),
    'M4': (2, 2, 2, 4, 4, 1, 1),
    'M5': (3, 3, 3, 2, 2, 3, 2),
}

def now():
    return dt.datetime.now(PARIS)

def serialized(data):
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + '\n'

def write_atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    try:
        tmp.write_text(serialized(data), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)

def load_courses(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or not isinstance(data.get('courses'), list):
        raise ValueError('Format invalide : {"courses": [...]} attendu')
    return data['courses']

def departure(course):
    instant = dt.datetime.fromisoformat(course['depart'])
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('Date de départ sans fuseau horaire')
    return instant.astimezone(PARIS)

def analyze(course, at):
    cid = str(course.get('id', 'inconnu'))
    try:
        start = departure(course)
        runners = course['partants']
        if not isinstance(runners, list) or len(runners) < 2:
            raise ValueError('Partants insuffisants')
        numbers = [int(h['numero']) for h in runners]
        if any(n <= 0 for n in numbers) or len(numbers) != len(set(numbers)):
            raise ValueError('Numéros invalides ou doublons')
        if not str(course.get('id', '')).strip() or cid == 'inconnu':
            raise ValueError('Identifiant de course manquant')
        if not str(course.get('source', '')).strip() or course['source'] == 'non_documentee':
            raise ValueError('Source de course non documentée')
        active = [h for h in runners if not h.get('non_partant', False)]
        if len(active) < 2:
            raise ValueError('Moins de deux partants actifs')
        for horse in active:
            if not str(horse.get('nom', '')).strip():
                raise ValueError('Nom de cheval manquant')
            for feature in FEATURES:
                if feature in horse:
                    val = horse[feature]
                    if isinstance(val, bool) or not isinstance(val, (float, int)) or not math.isfinite(val) or not 0 <= val <= 1:
                        raise ValueError('Score invalide : ' + feature)
        motors = {}
        for engine, weights in WEIGHTS.items():
            ranking = []
            for horse in active:
                present = [(float(horse[f]), w) for f, w in zip(FEATURES, weights) if f in horse]
                coverage = len(present) / len(FEATURES)
                score = sum(v * w for v, w in present) / sum(w for _, w in present) if present else 0
                ranking.append({'numero': int(horse['numero']), 'nom': horse['nom'], 'score': round(score, 4), 'couverture': round(coverage, 3)})
            ranking.sort(key=lambda h: (-h['score'], -h['couverture'], h['numero']))
            gap = ranking[0]['score'] - ranking[1]['score']
            eligible = ranking[0]['couverture'] >= .70 and ranking[1]['couverture'] >= .70 and gap >= .035 and at < start
            motors[engine] = {
                'classement': ranking[:5], 'gagnant_espere': ranking[0]['numero'],
                'place_espere': ranking[1]['numero'], 'ecart': round(gap, 4),
                'qualification': 'candidate' if eligible else 'abstention',
                'pari': 'aucun pari automatique',
            }
        return {'id': cid, 'hippodrome': course.get('hippodrome', ''), 'depart': start.isoformat(),
                'partants': len(active), 'non_partants': len(runners) - len(active),
                'source': course['source'], 'moteurs': motors,
                'statut': 'analyse_provisoire', 'analyse_a': at.isoformat()}
    except (KeyError, TypeError, ValueError, OverflowError, AttributeError) as exc:
        return {'id': cid, 'statut': 'rejetee', 'motif': str(exc), 'analyse_a': at.isoformat()}

def freeze(report, output_dir, date, at, safety_minutes):
    if report['statut'] != 'analyse_provisoire':
        return report
    start = dt.datetime.fromisoformat(report['depart'])
    if at >= start - dt.timedelta(minutes=safety_minutes):
        report['motif_fige'] = 'Délai de sécurité dépassé'
        return report
    if not any(m['qualification'] == 'candidate' for m in report['moteurs'].values()):
        report['motif_fige'] = 'Aucun moteur qualifié'
        return report
    safe_id = ''.join(c if c.isascii() and (c.isalnum() or c in '-_') else '_' for c in report['id'])[:65]
    identity = hashlib.sha256(report['id'].encode('utf-8')).hexdigest()[:16]
    folder = output_dir / 'figes' / date
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / (safe_id + '_' + identity + '.json')
    if target.exists():
        report['statut'] = 'fige_existant'
        report['fichier_fige'] = str(target)
        return report
    payload = json.dumps(report, ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf-8')
    digest = hashlib.sha256(payload).hexdigest()
    record = {'rapport': report, 'sha256': digest, 'fige_a': at.isoformat()}
    # O_EXCL interdit tout écrasement, même entre processus concurrents.
    try:
        with target.open('x', encoding='utf-8') as f:
            f.write(serialized(record))
            f.flush()
            os.fsync(f.fileno())
    except FileExistsError:
        report['statut'] = 'fige_existant'
    else:
        report['statut'] = 'fige'
    report['fichier_fige'] = str(target)
    return report

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--date', default=now().date().isoformat())
    parser.add_argument('--input', type=Path, default=Path('data/courses.json'))
    parser.add_argument('--output', type=Path, default=Path('data'))
    parser.add_argument('--figer', action='store_true')
    parser.add_argument('--diagnostic', action='store_true')
    parser.add_argument('--strict-input', action='store_true')
    parser.add_argument('--safety-minutes', type=int, default=2)
    args = parser.parse_args(argv)
    if args.diagnostic:
        print(json.dumps({'python': 'ok', 'timezone': str(PARIS), 'moteurs': list(WEIGHTS),
                          'collecte': 'fichier JSON externe requis', 'source_presente': args.input.is_file(),
                          'paris_reels': 'desactives'}, ensure_ascii=False))
        return 0
    try:
        dt.date.fromisoformat(args.date)
    except ValueError:
        parser.error('Date invalide (AAAA-MM-JJ)')
    if args.safety_minutes < 0:
        parser.error('--safety-minutes doit être positif')
    at = now()
    reports = []
    status = 'ok'
    if not args.input.is_file():
        if args.strict_input:
            parser.error('Fichier absent : ' + str(args.input))
        status = 'en_attente_donnees'
    else:
        try:
            courses = load_courses(args.input)
        except (OSError, ValueError) as exc:
            parser.error('Entrée illisible ou invalide : ' + str(exc))
        seen = set()
        for course in courses:
            if not isinstance(course, dict):
                reports.append({'statut': 'rejetee', 'motif': 'Course non objet'})
                continue
            try:
                if departure(course).date().isoformat() != args.date:
                    continue
            except (KeyError, TypeError, ValueError, OverflowError):
                pass
            cid = str(course.get('id', '')).strip()
            if cid and cid in seen:
                reports.append({'id': cid, 'statut': 'rejetee', 'motif': 'Identifiant de course dupliqué'})
                continue
            seen.add(cid)
            report = analyze(course, at)
            if args.figer:
                report = freeze(report, args.output, args.date, at, args.safety_minutes)
            reports.append(report)
        if not reports:
            status = 'aucune_course_verifiee'
    audit = {'date': args.date, 'genere_a': at.isoformat(), 'statut_collecte': status,
             'courses': reports, 'source': str(args.input) if args.input.is_file() else None,
             'avertissement': 'Aucune garantie de gain ; données vérifiées requises'}
    write_atomic(args.output / ('audit_' + args.date + '.json'), audit)
    write_atomic(args.output / 'audit.json', audit)
    print(json.dumps({'statut': status, 'courses': len(reports),
                      'figees': sum(r.get('statut') == 'fige' for r in reports)}, ensure_ascii=False))
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
