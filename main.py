#!/usr/bin/env python3
"""HIPPOFUSION X1000 M1-M5 — analyse prudente, figés et audits.

Usage:
 python main.py --diagnostic
 python main.py --date 2026-10-21 --input courses.json
 python main.py --date 2026-10-21 --input courses.json --figer

Format courses.json: {"courses":[{"id":"R1C1","hippodrome":"...",
 "depart":"2026-10-21T13:00:00+02:00","partants":[{"numero":1,
 "nom":"...","forme":0.7,"aptitude":0.8,"regularite":0.6,
 "jockey":0.5,"entraineur":0.5,"distance":0.6,"terrain":0.7}]}]}
Scores facultatifs entre 0 et 1, issus de sources vérifiées uniquement.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from zoneinfo import ZoneInfo

PARIS = ZoneInfo('Europe/Paris')
FEATURES = ('forme','aptitude','regularite','jockey','entraineur','distance','terrain')
WEIGHTS = {
 'M1': (4,3,3,1,1,2,2),
 'M2': (2,4,2,2,1,3,3),
 'M3': (3,2,4,2,2,1,1),
 'M4': (2,2,2,4,4,1,1),
 'M5': (3,3,3,2,2,3,2),
}

def now():
 return dt.datetime.now(PARIS)

def write_atomic(path, data):
 path.parent.mkdir(parents=True, exist_ok=True)
 tmp = path.with_name(path.name+'.tmp')
 tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding='utf-8')
 os.replace(tmp, path)

def load(path):
 data = json.loads(path.read_text(encoding='utf-8'))
 if not isinstance(data,dict) or not isinstance(data.get('courses'),list):
  raise ValueError('Format attendu: objet contenant une liste courses')
 return data['courses']

def departure(course):
 t = dt.datetime.fromisoformat(course['depart'])
 if t.tzinfo is None:
  raise ValueError('Départ sans fuseau horaire')
 return t.astimezone(PARIS)

def analyze(course, at):
 cid = str(course.get('id','inconnu'))
 try:
  start = departure(course)
  runners = course['partants']
  if not isinstance(runners,list) or len(runners)<2: raise ValueError('Partants insuffisants')
  nums = [int(h['numero']) for h in runners]
  if len(nums)!=len(set(nums)): raise ValueError('Numéros en double')
  active = [h for h in runners if not h.get('non_partant',False)]
  if len(active)<2: raise ValueError('Partants actifs insuffisants')
  for h in active:
   if not str(h.get('nom','')).strip(): raise ValueError('Nom manquant')
   for f in FEATURES:
    if f in h and (isinstance(h[f],bool) or not isinstance(h[f],(int,float)) or not 0<=h[f]<=1):
     raise ValueError('Score invalide: '+f)
  results={}
  for engine,weights in WEIGHTS.items():
   ranked=[]
   for h in active:
    known=[(float(h[f]),w) for f,w in zip(FEATURES,weights) if f in h]
    coverage=len(known)/len(FEATURES)
    score=sum(v*w for v,w in known)/sum(w for _,w in known) if known else 0
    ranked.append({'numero':int(h['numero']),'nom':h['nom'],
                   'score':round(score,4),'couverture':round(coverage,3)})
   ranked.sort(key=lambda x:(-x['score'],-x['couverture'],x['numero']))
   leader=ranked[0]
   # Abstention si couverture insuffisante ou classement trop serré.
   margin=leader['score']-ranked[1]['score']
   qualified=(leader['couverture']>=0.70 and ranked[1]['couverture']>=0.70
              and margin>=0.035 and at<start)
   results[engine]={'classement':ranked[:5], 'gagnant_espere':leader['numero'],
     'place_espere':ranked[1]['numero'], 'ecart':round(margin,4),
     'qualification':'candidate' if qualified else 'abstention',
     'pari':'aucun pari automatique'}
  return {'id':cid,'hippodrome':course.get('hippodrome',''),
          'depart':start.isoformat(),'partants':len(active),'moteurs':results,
          'statut':'analyse_provisoire','analyse_a':at.isoformat()}
 except (KeyError,TypeError,ValueError,OverflowError) as exc:
  return {'id':cid,'statut':'rejetee','motif':str(exc),'analyse_a':at.isoformat()}

def main():
 p=argparse.ArgumentParser(description=__doc__)
 p.add_argument('--date',default=now().date().isoformat())
 p.add_argument('--input',type=Path,help='JSON local de courses vérifiées')
 p.add_argument('--output',type=Path,default=Path('rapports'))
 p.add_argument('--figer',action='store_true')
 p.add_argument('--diagnostic',action='store_true')
 args=p.parse_args()
 if args.diagnostic:
  print(json.dumps({'python':'ok','timezone':str(PARIS), 'moteurs':list(WEIGHTS),
    'collecte':'non configuree: fournir --input','paris_reels':'desactives'},ensure_ascii=False)); return
 try: dt.date.fromisoformat(args.date)
 except ValueError: p.error('Date invalide (AAAA-MM-JJ)')
 if not args.input or not args.input.is_file():
  p.error('Aucune donnée vérifiée: fournir --input courses.json')
 try: courses=load(args.input)
 except (OSError,ValueError,json.JSONDecodeError) as exc: p.error(str(exc))
 timestamp=now()
 output=[]
 for course in courses:
  if not isinstance(course,dict):
   output.append({'statut':'rejetee','motif':'Course non objet'}); continue
  try:
   if departure(course).date().isoformat()!=args.date: continue
  except (KeyError,TypeError,ValueError): pass
  report=analyze(course,timestamp)
  if args.figer and report['statut']=='analyse_provisoire':
   start=dt.datetime.fromisoformat(report['depart'])
   # Pas de figé si le départ est dans moins de 2 minutes.
   if timestamp < start-dt.timedelta(minutes=2):
    blob=json.dumps(report,sort_keys=True,ensure_ascii=False).encode()
    digest=hashlib.sha256(blob).hexdigest()
    safe_id=''.join(c if c.isalnum() or c in '-_' else '_' for c in report['id'])
    target=args.output/'figes'/args.date/(safe_id+'_'+digest[:12]+'.json')
    if not target.exists():
     write_atomic(target,{'rapport':report,'sha256':digest,'fige_a':timestamp.isoformat()})
    report['statut']='fige'; report['fichier_fige']=str(target)
   else: report['motif_fige']='Délai de sécurité dépassé'
  output.append(report)
 final={'date':args.date,'genere_a':timestamp.isoformat(),'courses':output,
        'source':str(args.input),'avertissement':'Scores indicatifs; aucune garantie de gain'}
 target=args.output/('audit_'+args.date+'.json')
 write_atomic(target,final)
 print(json.dumps({'audit':str(target),'courses':len(output),
  'figees':sum(c.get('statut')=='fige' for c in output)},ensure_ascii=False))

if __name__=='__main__': main()
