import itertools,json,statistics,time,urllib.parse,urllib.request
BASE="https://demo-fapi.binance.com/fapi/v1/klines";rows=[];end=None
for _ in range(18):
 p={"symbol":"BTCUSDT","interval":"5m","limit":1500}
 if end is not None:p["endTime"]=end
 with urllib.request.urlopen(BASE+"?"+urllib.parse.urlencode(p),timeout=20) as r:b=json.load(r)
 rows=b+rows;end=int(b[0][0])-1
rows=rows[:-1];O=[float(x[1]) for x in rows];H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];C=[float(x[4]) for x in rows]
AT=[0.0]*len(rows)
for i in range(14,len(rows)):
 AT[i]=sum(max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-13,i+1))/14
START=100.0;QTY=.001;FEE=.0004;SLIP=.0001
def sim(cfg,a,b,initial=START):
 look,stop_atr,rr=cfg;bal=initial;peak=bal;dd=0.;pos=0;entry=stop=target=risk=0.;trades=[];skipped=0;day=None;day_start=bal;daily_blocked=0
 for i in range(max(a,look,20),b-1):
  current_day=int(rows[i][0])//86400000
  if current_day!=day:day=current_day;day_start=bal
  sig=1 if C[i]>max(H[i-look:i]) else -1 if C[i]<min(L[i-look:i]) else 0
  if pos:
   raw=None
   if (pos>0 and L[i+1]<=stop) or (pos<0 and H[i+1]>=stop):raw=stop;reason="STOP"
   elif (pos>0 and H[i+1]>=target) or (pos<0 and L[i+1]<=target):raw=target;reason="TARGET"
   if raw is not None:
    exit_price=raw*(1-SLIP if pos>0 else 1+SLIP);gross=pos*(exit_price-entry)*QTY;fees=(entry+exit_price)*QTY*FEE;net=gross-fees;bal+=net;trades.append({"pnl":net,"r":gross/(risk*QTY) if risk else 0,"reason":reason});pos=0;peak=max(peak,bal);dd=max(dd,(peak-bal)/peak)
  if not pos and sig and bal<=day_start-2:daily_blocked+=1
  if not pos and sig and bal>day_start-2:
   entry=O[i+1]*(1+SLIP if sig>0 else 1-SLIP);notional=entry*QTY
   if notional>bal:skipped+=1;continue
   pos=sig;risk=max(AT[i]*stop_atr,entry*.004);stop=entry-pos*risk;target=entry+pos*risk*rr
 w=[x for x in trades if x["pnl"]>0];loss=[x for x in trades if x["pnl"]<=0];gw=sum(x["pnl"] for x in w);gl=abs(sum(x["pnl"] for x in loss))
 return {"startBalance":initial,"endBalance":round(bal,2),"pnl":round(bal-initial,2),"returnPct":round((bal/initial-1)*100,2),"trades":len(trades),"wins":len(w),"winRate":round(len(w)/len(trades)*100,1) if trades else 0,"profitFactor":round(gw/gl,2) if gl else None,"expectancyUsd":round(sum(x["pnl"] for x in trades)/len(trades),3) if trades else None,"expectancyR":round(sum(x["r"] for x in trades)/len(trades),2) if trades else None,"maxDrawdown":round(dd*100,2),"skippedNoMargin":skipped}
cut=int(len(rows)*.7);space=list(itertools.product([6,8,10,12,16,20,30,40],[1.5,2.,2.5,3.,3.5],[1.5,2.,2.5,3.,3.5,4.]))
rank=[]
for cfg in space:
 tr=sim(cfg,0,cut)
 if tr["trades"]>=30 and tr["pnl"]>0 and (tr["profitFactor"] or 0)>=1.1 and tr["expectancyUsd"]>0:rank.append((tr["expectancyUsd"]-tr["maxDrawdown"]*.01,cfg,tr))
rank.sort(reverse=True);chosen=rank[0] if rank else None
out={"generatedAt":int(time.time()*1000),"capital":100,"quantity":.001,"leverage":1,"feePerSidePct":.04,"slippagePerFillPct":.01,"candles":len(rows),"trainCandles":cut,"testCandles":len(rows)-cut,"searched":len(space),"trainQualified":len(rank),"currentV2":{"config":{"lookback":8,"stopAtr":3,"riskReward":3},"train":sim((8,3.,3.),0,cut),"test":sim((8,3.,3.),cut,len(rows)),"full":sim((8,3.,3.),0,len(rows))}}
if chosen:
 _,cfg,tr=chosen;out["selected"]={"config":{"lookback":cfg[0],"stopAtr":cfg[1],"riskReward":cfg[2]},"train":tr,"test":sim(cfg,cut,len(rows)),"full":sim(cfg,0,len(rows))}
print(json.dumps(out,separators=(",",":")))