import argparse,json,statistics,time,urllib.parse,urllib.request
S="BTCUSDT";R="https://fapi.binance.com";D="https://demo-fapi.binance.com"
def g(b,p,x={}):
 q=urllib.parse.urlencode(x)
 with urllib.request.urlopen(b+p+("?"+q if q else ""),timeout=12) as r:return json.load(r)
def safe(f,d):
 try:return f()
 except:return d
def ema(v,n):
 a=2/(n+1);o=[v[0]]
 for x in v[1:]:o.append(x*a+o[-1]*(1-a))
 return o
def tech(rows):
 c=[float(x[4]) for x in rows];v=[float(x[5]) for x in rows];p=c[-1];s20=sum(c[-20:])/20;s50=sum(c[-50:])/50
 ds=[b-a for a,b in zip(c,c[1:])];ga=sum(max(x,0) for x in ds[-14:])/14;lo=sum(max(-x,0) for x in ds[-14:])/14;rsi=100 if not lo else 100-100/(1+ga/lo)
 ef,es=ema(c,12),ema(c,26);m=[a-b for a,b in zip(ef,es)];mh=m[-1]-ema(m,9)[-1];roc=(p/c[-4]-1)*100
 tr=[max(float(b[2])-float(b[3]),abs(float(b[2])-float(a[4])),abs(float(b[3])-float(a[4]))) for a,b in zip(rows,rows[1:])];atr=sum(tr[-14:])/14;ap=atr/p*100
 base=v[-21:-1];z=(v[-1]-statistics.mean(base))/(statistics.pstdev(base) or 1);cd=1 if float(rows[-1][4])>float(rows[-1][1]) else -1
 def f(k,l,x,val,u,n):return {"key":k,"label":l,"vote":x,"value":round(val,5),"unit":u,"detail":n}
 fs=[f("trend","Xu hướng SMA",1 if s20>s50 else -1,(s20-s50)/p*100,"%","SMA20 so với SMA50"),f("rsi","RSI 14",1 if rsi>=55 else -1 if rsi<=45 else 0,rsi,"","45–55 là trung tính"),f("macd","MACD",1 if mh>0 else -1,mh,"","Histogram"),f("momentum","Động lượng",1 if roc>.12 else -1 if roc<-.12 else 0,roc,"%","3 nến"),f("volume","Volume",cd if z>=.5 else 0,z,"σ","So với 20 nến"),f("volatility","Biến động",cd if .12<=ap<=1.5 else 0,ap,"%","ATR14")]
 return {"price":p,"atr":atr,"atrPct":ap,"roc":roc,"factors":fs}
def analyze(mode):
 b=D if mode=="futures_demo" else R;rows=g(b,"/fapi/v1/klines",{"symbol":S,"interval":"5m","limit":501})[:-1];t=tech(rows)
 dep=safe(lambda:g(b,"/fapi/v1/depth",{"symbol":S,"limit":100}),{"bids":[],"asks":[]});bv=sum(float(p)*float(q) for p,q in dep["bids"]);av=sum(float(p)*float(q) for p,q in dep["asks"]);imb=(bv-av)/(bv+av) if bv+av else 0
 fund=float(safe(lambda:g(R,"/fapi/v1/premiumIndex",{"symbol":S}),{}).get("lastFundingRate",0));oi=safe(lambda:g(R,"/futures/data/openInterestHist",{"symbol":S,"period":"5m","limit":3}),[]);oc=(float(oi[-1]["sumOpenInterest"])/float(oi[-2]["sumOpenInterest"])-1)*100 if len(oi)>1 else 0
 rr=safe(lambda:g(R,"/futures/data/topLongShortPositionRatio",{"symbol":S,"period":"5m","limit":1}),[]);ratio=float(rr[-1]["longShortRatio"]) if rr else 1;pd=1 if t["roc"]>0 else -1
 def f(k,l,x,v,u,n):return {"key":k,"label":l,"vote":x,"value":round(v,5),"unit":u,"detail":n}
 fs=t["factors"]+[f("orderbook","Order book",1 if imb>.08 else -1 if imb<-.08 else 0,imb*100,"%","100 mức"),f("funding","Funding",-1 if fund>.0001 else 1 if fund<-.0001 else 0,fund*100,"%","Contrarian"),f("open_interest","Open interest",pd if abs(oc)>=.15 else 0,oc,"%","Xác nhận hướng"),f("top_traders","Top traders",1 if ratio>1.05 else -1 if ratio<.95 else 0,ratio,"x","Long/Short")]
 ts=sum(x["vote"] for x in t["factors"]);sc=sum(x["vote"] for x in fs);v="LONG" if sc>=4 and ts>=2 else "SHORT" if sc<=-4 and ts<=-2 else "WAIT";di=1 if v=="LONG" else -1 if v=="SHORT" else 0;dist=max(t["atr"]*1.5,t["price"]*.004)
 return {"symbol":S,"timeframe":"5m","candle":int(rows[-1][0]),"generatedAt":int(time.time()*1000),"verdict":v,"score":sc,"maxScore":len(fs),"confidence":round(abs(sc)/len(fs)*100),"longVotes":sum(x["vote"]>0 for x in fs),"shortVotes":sum(x["vote"]<0 for x in fs),"neutralVotes":sum(x["vote"]==0 for x in fs),"price":t["price"],"entryLow":t["price"]-t["atr"]*.15,"entryHigh":t["price"]+t["atr"]*.15,"stop":t["price"]-di*dist if di else None,"target":t["price"]+di*dist*2 if di else None,"riskReward":2 if di else None,"atr":t["atr"],"atrPct":t["atrPct"],"factors":fs,"model":"Rule-based 10-factor consensus v1"}
if __name__=="__main__":
 p=argparse.ArgumentParser();p.add_argument("--mode",choices=["real","futures_demo"],default="real");print(json.dumps(analyze(p.parse_args().mode),separators=(",",":")))