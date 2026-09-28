import itertools,json,math,random,statistics,urllib.parse,urllib.request
BASE="https://demo-fapi.binance.com/fapi/v1/klines"
rows=[];end=None
for _ in range(6):
 params={"symbol":"BTCUSDT","interval":"5m","limit":1500}
 if end is not None:params["endTime"]=end
 with urllib.request.urlopen(BASE+"?"+urllib.parse.urlencode(params),timeout=20) as r: batch=json.load(r)
 if not batch:break
 rows=batch+rows;end=int(batch[0][0])-1
rows=rows[:-1]
C=[float(x[4]) for x in rows];O=[float(x[1]) for x in rows];H=[float(x[2]) for x in rows];L=[float(x[3]) for x in rows];V=[float(x[5]) for x in rows]
def ema(v,n):
 a=2/(n+1);o=[v[0]]
 for x in v[1:]:o.append(x*a+o[-1]*(1-a))
 return o
ef,es=ema(C,12),ema(C,26);mac=[a-b for a,b in zip(ef,es)];ms=ema(mac,9);MH=[a-b for a,b in zip(mac,ms)]
RS=[50.0]*len(C);AT=[0.0]*len(C);VZ=[0.0]*len(C)
for i in range(20,len(C)):
 ds=[C[j]-C[j-1] for j in range(i-13,i+1)];g=sum(max(x,0) for x in ds)/14;l=sum(max(-x,0) for x in ds)/14;RS[i]=100 if not l else 100-100/(1+g/l)
 tr=[max(H[j]-L[j],abs(H[j]-C[j-1]),abs(L[j]-C[j-1])) for j in range(i-13,i+1)];AT[i]=sum(tr)/14
 b=V[i-20:i];VZ[i]=(V[i]-statistics.mean(b))/(statistics.pstdev(b) or 1)
def run(cfg,a,b):
 fast,slow,rsi,mom,threshold,sl,rr,family=cfg;eq=peak=1.;dd=0.;pos=0;entry=stop=target=risk=0.;ret=[];rvals=[]
 for i in range(max(a,slow,fast,25),b-1):
  sf=sum(C[i-fast+1:i+1])/fast;ss=sum(C[i-slow+1:i+1])/slow;trend=1 if sf>ss else -1
  rv=1 if RS[i]>=rsi else -1 if RS[i]<=100-rsi else 0;mv=1 if (C[i]/C[i-3]-1)*100>mom else -1 if (C[i]/C[i-3]-1)*100<-mom else 0
  macv=1 if MH[i]>0 else -1;vv=(1 if C[i]>O[i] else -1) if VZ[i]>.4 else 0
  if family=="breakout":
   sig=1 if C[i]>max(H[i-fast:i]) else -1 if C[i]<min(L[i-fast:i]) else 0
  else:
   score=trend+rv+mv+macv+vv if family=="trend" else -rv-mv+trend+macv+vv
   sig=1 if score>=threshold else -1 if score<=-threshold else 0
  out=None
  if pos:
   if (pos>0 and L[i+1]<=stop) or (pos<0 and H[i+1]>=stop):out=stop
   elif (pos>0 and H[i+1]>=target) or (pos<0 and L[i+1]<=target):out=target

   if out is not None:
    x=pos*(out/entry-1)-.0008;eq*=1+x;ret.append(x);rvals.append(pos*(out-entry)/risk);pos=0;peak=max(peak,eq);dd=max(dd,(peak-eq)/peak)
  if not pos and sig and .08<=AT[i]/C[i]*100<=1.5:
   pos=sig;entry=O[i+1];risk=max(AT[i]*sl,entry*.003);stop=entry-pos*risk;target=entry+pos*risk*rr
 w=[x for x in ret if x>0];loss=[x for x in ret if x<=0];gl=abs(sum(loss))
 return {"trades":len(ret),"winRate":len(w)/len(ret)*100 if ret else 0,"netReturn":(eq-1)*100,"profitFactor":sum(w)/gl if gl else 99,"expectancyR":sum(rvals)/len(rvals) if rvals else -99,"maxDrawdown":dd*100}
space=list(itertools.product([8,12,20],[30,50,80],[52,55,58],[.05,.1,.15],[1,2,3],[.8,1.,1.5,2.],[1.5,2.,2.5],["trend","reversion","breakout"]))
random.Random(42).shuffle(space);space=list(itertools.product([20,50],[100,200],[52,55],[.05,.1],[2,3],[1.5,2.,2.5],[1.5,2.,2.5],["trend","reversion"]))+list(itertools.product([8,12,20,30,50,80],[30],[52],[.05],[1],[.8,1.,1.5,2.,2.5,3.],[1.,1.5,2.,2.5,3.,3.5,4.],["breakout"]))+space;cut=int(len(C)*.7);tested=[]
for cfg in space[:1600]:
 tr=run(cfg,0,cut)
 if tr["trades"]>=20 and tr["profitFactor"]>=1.1 and tr["expectancyR"]>0 and tr["netReturn"]>0: tested.append((tr["expectancyR"]+min(tr["profitFactor"],3)/10,cfg,tr))
tested.sort(reverse=True);valid=[]
for _,cfg,tr in tested[:120]:
 te=run(cfg,cut,len(C))
 if te["trades"]>=12 and te["profitFactor"]>=1.2 and te["expectancyR"]>0 and te["netReturn"]>0 and te["maxDrawdown"]<=10:valid.append((te["expectancyR"],cfg,tr,te))
valid.sort(reverse=True)
out={"candles":len(C),"trainCandles":cut,"testCandles":len(C)-cut,"searched":1600,"trainCandidates":len(tested),"passed":len(valid)}
if valid:
 _,cfg,tr,te=valid[0];out.update({"config":{"fast":cfg[0],"slow":cfg[1],"rsi":cfg[2],"momentumPct":cfg[3],"scoreThreshold":cfg[4],"stopAtr":cfg[5],"riskReward":cfg[6],"family":cfg[7]},"train":tr,"test":te})
elif tested:
 _,cfg,tr=tested[0];out.update({"bestRejected":{"config":cfg,"train":tr,"test":run(cfg,cut,len(C))}})
print(json.dumps(out,separators=(",",":")))