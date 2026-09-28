"""Two-way BTCUSDT M5 consensus bot for Binance Futures Demo."""
import hashlib,hmac,json,os,time,urllib.parse,urllib.request
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
from strategy_engine import analyze
B="https://demo-fapi.binance.com";S="BTCUSDT";P=Path(__file__).with_name("state-futures_demo.json");D=Decimal
def api(method,path,p=None,signed=False):
 p=dict(p or {});h={}
 if signed:
  key=os.getenv("BINANCE_FUTURES_DEMO_API_KEY");secret=os.getenv("BINANCE_FUTURES_DEMO_API_SECRET")
  if not key or not secret:raise RuntimeError("Futures Demo credentials are missing")
  p.update(timestamp=int(time.time()*1000),recvWindow=5000);q=urllib.parse.urlencode(p);p["signature"]=hmac.new(secret.encode(),q.encode(),hashlib.sha256).hexdigest();h["X-MBX-APIKEY"]=key
 q=urllib.parse.urlencode(p);u=B+path+("?"+q if q else "")
 with urllib.request.urlopen(urllib.request.Request(u,headers=h,method=method),timeout=15) as r:return json.load(r)
def load():
 if P.exists():
  x=json.loads(P.read_text(encoding="utf-8"))
  if "managed_side" not in x:x["managed_side"]="LONG" if x.get("managed_position") else None
  return x
 return {"last_candle":None,"history":[],"managed_side":None}
def save(x):
 t=P.with_suffix(".tmp");t.write_text(json.dumps(x,indent=2)+"\n",encoding="utf-8");t.replace(P)
def rules():
 x=next(x for x in api("GET","/fapi/v1/exchangeInfo")["symbols"] if x["symbol"]==S);f={y["filterType"]:y for y in x["filters"]};lot=f["MARKET_LOT_SIZE"];return D(lot["stepSize"]),D(lot["minQty"]),D(f["MIN_NOTIONAL"]["notional"])
def floor(x,s):return (x/s).to_integral_value(rounding=ROUND_DOWN)*s
def account():
 a=api("GET","/fapi/v2/account",signed=True);p=[x for x in a["positions"] if x["symbol"]==S]
 if not a.get("canTrade") or a.get("multiAssetsMargin") or len(p)!=1 or p[0]["positionSide"]!="BOTH":raise RuntimeError("Demo requires tradable single-asset one-way mode")
 return a,p[0]
def order(state,candle,action,side,qty,price,reduce=False,reason="signal"):
 cid=f"map-{candle}-{int(time.time())}-{side.lower()}";event={"time":int(time.time()*1000),"candle":candle,"signal":action,"action":action,"side":side,"price":str(price),"quantity":str(qty),"reason":reason}
 state["pending_order"]={"client_id":cid,"event":event};save(state)
 p={"symbol":S,"side":side,"type":"MARKET","quantity":format(qty,"f"),"newClientOrderId":cid,"newOrderRespType":"RESULT"}
 if reduce:p["reduceOnly"]="true"
 r=api("POST","/fapi/v1/order",p,signed=True)
 if r.get("status")!="FILLED":raise RuntimeError(f"Order {cid} status={r.get('status')}; reconcile manually")
 fill=D(r.get("avgPrice") or price);event.update(status="FILLED",orderId=r.get("orderId"),price=str(fill))
 state["history"]=([event]+state.get("history",[]))[:200];state.pop("pending_order",None);save(state);return fill
def main():
 state=load()
 if state.get("pending_order"):raise RuntimeError("Pending Demo order requires reconciliation")
 m=analyze("futures_demo");candle=str(m["candle"]);price=D(str(m["price"]));a,p=account();amt=D(p["positionAmt"]);side="LONG" if amt>0 else "SHORT" if amt<0 else None
 if side and state.get("managed_side")!=side:raise RuntimeError("Unmanaged Demo position; no order sent")
 if not side and state.get("managed_side"):state["managed_side"]=None;save(state)
 step,mq,mn=rules();reason=None
 if side:
  if side=="LONG" and price<=D(str(state.get("stop",0))) or side=="SHORT" and price>=D(str(state.get("stop",10**20))):reason="stop"
  elif side=="LONG" and price>=D(str(state.get("target",10**20))) or side=="SHORT" and price<=D(str(state.get("target",0))):reason="target"
  elif m["verdict"] not in (side,"WAIT"):reason="opposite"
 if reason:
  close_side="SELL" if side=="LONG" else "BUY";qty=floor(abs(amt),step);entry=D(str(state["entry_price"]));fill=order(state,candle,"CLOSE_"+side,close_side,qty,price,True,reason);pnl=(fill-entry)*qty*(D(1) if side=="LONG" else D(-1));state["history"][0]["pnl"]=str(pnl);state["managed_side"]=None;state["last_candle"]=candle;save(state);print(f"Closed {side} pnl={pnl} reason={reason}");return
 if state.get("last_candle")==candle:print("Already processed this candle");return
 if not side and m["verdict"] in ("LONG","SHORT"):
  if int(time.time()*1000)-(int(candle)+300000)>120000:raise RuntimeError("Stale entry signal")
  if api("GET","/fapi/v1/openOrders",{"symbol":S},signed=True):raise RuntimeError("Open Demo orders found")
  if p.get("marginType")!="isolated":api("POST","/fapi/v1/marginType",{"symbol":S,"marginType":"ISOLATED"},signed=True)
  if int(p["leverage"])!=1:api("POST","/fapi/v1/leverage",{"symbol":S,"leverage":1},signed=True)
  qty=floor(min(D("60"),D(a["availableBalance"])*D(".9"))/price,step)
  if qty<mq or qty*price<mn:raise RuntimeError("Below Demo minimum notional")
  oside="BUY" if m["verdict"]=="LONG" else "SELL";fill=order(state,candle,"OPEN_"+m["verdict"],oside,qty,price)
  dist=max(D(str(m["atr"]))*D("1.5"),fill*D(".004"));direction=D(1) if m["verdict"]=="LONG" else D(-1);state.update(managed_side=m["verdict"],entry_price=str(fill),stop=str(fill-direction*dist),target=str(fill+direction*dist*2))
 state["last_candle"]=candle;state["history"]=([{"time":int(time.time()*1000),"candle":candle,"signal":m["verdict"],"action":"HOLD","price":str(price),"score":m["score"]}]+state.get("history",[]))[:200];save(state);print(f"Decision={m['verdict']} score={m['score']}/{m['maxScore']} position={side or 'FLAT'}")
if __name__=="__main__":main()