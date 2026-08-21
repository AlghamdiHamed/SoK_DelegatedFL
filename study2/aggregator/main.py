#!/usr/bin/env python3
from __future__ import annotations
import argparse, hashlib, json, signal, socket, threading, time
from pathlib import Path
import numpy as np
import torch
from study2.common.models import FullMLP, arrays_from_state, average_states
from study2.common.resources import Monitor
from study2.common.wire import Header, MessageLogger, decode_arrays, encode_arrays, pack_meta, receive, send, unpack_meta


class Aggregator:
    def __init__(self,args):
        self.args=args;self.lock=threading.Condition();self.stopping=False;torch.manual_seed(args.seed);self.state=arrays_from_state(FullMLP());self.round=1;self.contributions={};self.dispatch_ns={}
        self.logger=MessageLogger(args.run_dir/"messages_A.jsonl");self.metrics=(args.run_dir/"aggregator_rounds.jsonl").open("x",encoding="utf-8")
    def reply(self,conn,req,kind,payload):send(conn,Header.make("A",req.sender,req.task_id,req.client_id,req.round_id,kind,payload),payload,self.logger)
    def handle(self,conn):
        try:
            req,payload,_=receive(conn,self.logger);meta,binary=unpack_meta(payload)
            with self.lock:
                if req.message_type=="GET_MODEL":
                    while not self.stopping and self.round<int(req.round_id):self.lock.wait()
                    if self.stopping:raise RuntimeError("aggregator stopping")
                    if self.round!=int(req.round_id):raise ValueError("requested completed/stale round")
                    self.dispatch_ns.setdefault(self.round,time.perf_counter_ns())
                    out=pack_meta({"round_id":self.round,"model_state_hash":hashlib.sha256(encode_arrays(self.state)).hexdigest()},encode_arrays(self.state));self.reply(conn,req,"GLOBAL_MODEL",out)
                elif req.message_type=="CONTRIBUTION":
                    if req.round_id!=self.round or req.client_id not in ("O_1","O_2","O_3"):raise ValueError("wrong round/client")
                    if req.client_id in self.contributions:raise ValueError("duplicate contribution")
                    self.contributions[req.client_id]=(int(meta["sample_count"]),decode_arrays(binary),int(meta["owner_protocol_online_ns"]))
                    completed=False
                    if len(self.contributions)==3:
                        old=self.round;self.state=average_states([(n,s) for n,s,_ in self.contributions.values()]);online={k:v[2] for k,v in self.contributions.items()};self.contributions={};self.round+=1;completed=True
                        with (self.args.run_dir/f"global_model_round_{old:02d}.bin").open("xb") as f:f.write(encode_arrays(self.state))
                        self.metrics.write(json.dumps({"round_id":old,"owner_protocol_online_ns":online,"end_to_end_round_ns":time.perf_counter_ns()-self.dispatch_ns[old]},sort_keys=True,separators=(",",":"))+"\n");self.metrics.flush();self.lock.notify_all()
                    self.reply(conn,req,"CONTRIBUTION_ACCEPTED",pack_meta({"completed_round":completed,"next_round":self.round},b""))
                else:raise ValueError("unsupported message")
        except Exception as exc:
            try:self.reply(conn,req,"ERROR",pack_meta({"error":repr(exc)},b""))
            except Exception:pass
        finally:conn.close()

def main():
    process_start=time.perf_counter_ns();p=argparse.ArgumentParser();p.add_argument("--listen",default="0.0.0.0:7002");p.add_argument("--run-dir",type=Path,required=True);p.add_argument("--seed",type=int,required=True);a=p.parse_args();host,port=a.listen.rsplit(":",1)
    with Monitor(a.run_dir/"resources_A.json"):
        server=Aggregator(a)
        with socket.create_server((host,int(port)),reuse_port=False) as s:
            with (a.run_dir/"cold_start_A.json").open("x",encoding="utf-8") as f:json.dump({"boundary":"module_main_entry_to_listening_socket","cold_start_ns":time.perf_counter_ns()-process_start},f,sort_keys=True);f.write("\n")
            stop=threading.Event();workers=[]
            def shutdown(_signum,_frame):
                stop.set()
                with server.lock:
                    server.stopping=True
                    server.lock.notify_all()
                try:s.shutdown(socket.SHUT_RDWR)
                except OSError:pass
                s.close()
            signal.signal(signal.SIGTERM,shutdown)
            signal.signal(signal.SIGINT,shutdown)
            s.settimeout(.5)
            while server.round<=10:
                try:conn,_=s.accept()
                except TimeoutError:continue
                except OSError:
                    if stop.is_set():break
                    raise
                worker=threading.Thread(target=server.handle,args=(conn,))
                worker.start();workers.append(worker)
            with server.lock:
                server.stopping=True
                server.lock.notify_all()
            for worker in workers:worker.join()
    server.logger.close();server.metrics.close()
if __name__=="__main__":main()
