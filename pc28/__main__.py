import argparse,json
from pathlib import Path
from .service import Service
from .server import serve

def main():
 p=argparse.ArgumentParser(description='PC28本地采集与模拟研究平台');p.add_argument('--root',default=str(Path(__file__).resolve().parent.parent));sub=p.add_subparsers(dest='command',required=True)
 sub.add_parser('serve');sub.add_parser('status');sub.add_parser('collect');sub.add_parser('archive');sub.add_parser('backup');sub.add_parser('export_draws');sub.add_parser('sync')
 q=sub.add_parser('import');q.add_argument('path')
 q=sub.add_parser('backtest');q.add_argument('--start');q.add_argument('--end');q.add_argument('--unlimited',action='store_true');q.add_argument('--direction',choices=['大','小','单','双']);q.add_argument('--threshold',type=int);q.add_argument('--max-bets',type=int);q.add_argument('--loss-multiplier',type=float);q.add_argument('--max-losses',type=int);q.add_argument('--base-stake',type=str)
 q=sub.add_parser('select');q.add_argument('--month',required=True)
 q=sub.add_parser('walkforward');q.add_argument('--start',required=True);q.add_argument('--end',required=True)
 args=p.parse_args()
 if args.command=='serve':serve(args.root);return
 s=Service(args.root)
 if args.command=='status':result=s.status()
 else:
  params=vars(args).copy()
  if args.command=='backtest':
   st=s.cfg['simulation']['fixed'].copy()
   for key in ('direction','threshold','max_bets','max_losses'):
    if params.get(key) is not None:st[key]=params[key]
   if args.loss_multiplier is not None:
    from .model import money
    st['multiplier_hundredths']=money(args.loss_multiplier)
   if args.base_stake is not None:
    from .model import money
    st['base_cents']=money(args.base_stake)
   params['strategy']=st;params['with_risk_limits']=not args.unlimited
  result=s.execute(args.command,params)
 print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
