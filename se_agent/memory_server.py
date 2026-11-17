"""Local memory browser/API, bound to a server-configured identity and stage.

Never expose to an untrusted network. Production transport must authenticate
sessions before constructing MemorySession; request bodies cannot select users.
"""
import argparse
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
from .domain_memory import DomainMemory, MemorySession

PAGE = '''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>SQLite 领域记忆</title>
<style>body{font:16px system-ui;max-width:1100px;margin:40px auto;background:#f5f7fb;color:#182235}input,button{padding:10px;margin:5px}pre{white-space:pre-wrap;background:white;padding:24px;border-radius:12px}</style>
<h1>SQLite 领域记忆浏览器</h1><p>由服务端固定当前项目、身份和阶段。仅显示获准内容。</p>
<input id="domain" placeholder="领域"><input id="terms" placeholder="标签或同义词，逗号分隔"><button id="search">检索经验</button><button id="all">查看可见记录</button><pre id="result">请选择查询</pre>
<script>async function show(path){try{let r=await fetch(path);document.getElementById('result').textContent=JSON.stringify(await r.json(),null,2)}catch(e){document.getElementById('result').textContent=String(e)}}document.getElementById('all').onclick=()=>show('/api/memory/records');document.getElementById('search').onclick=()=>show('/api/memory/search?domain='+encodeURIComponent(document.getElementById('domain').value)+'&terms='+encodeURIComponent(document.getElementById('terms').value));</script></html>'''


def make_server(path, project, agent, stage, port=8004):
    class Handler(BaseHTTPRequestHandler):
        def respond(self, code, value, content_type='application/json; charset=utf-8'):
            data=(json.dumps(value,ensure_ascii=False) if content_type.startswith('application/json') else value).encode()
            self.send_response(code)
            self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            parsed=urlparse(self.path)
            if parsed.path == '/':
                return self.respond(200,PAGE,'text/html; charset=utf-8')
            db=DomainMemory(path)
            try:
                session=MemorySession(db,project,agent,stage)
                query=parse_qs(parsed.query)
                if parsed.path == '/api/memory/records':
                    data=db.list_records(project,agent,stage)
                elif parsed.path == '/api/memory/search':
                    data=session.search(query.get('domain',[''])[0],query.get('terms',[''])[0].split(','))
                elif parsed.path == '/api/memory/record':
                    data=session.read(dict(id=query['id'][0],version=int(query['version'][0])))
                else:
                    return self.respond(404,{'error':'NOT_FOUND'})
                self.respond(200,{'items':data})
            except PermissionError:
                self.respond(403,{'error':'FORBIDDEN'})
            except KeyError:
                self.respond(404,{'error':'NOT_FOUND'})
            except (ValueError,TypeError) as exc:
                self.respond(400,{'error':str(exc)})
            finally:
                db.close()

        def log_message(self,*args):
            pass
    return HTTPServer(('127.0.0.1',port),Handler)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--db',required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--agent',required=True)
    parser.add_argument('--stage',default='review')
    parser.add_argument('--port',type=int,default=8004)
    args=parser.parse_args()
    with make_server(args.db,args.project,args.agent,args.stage,args.port) as server:
        print(f'Memory browser http://127.0.0.1:{server.server_port}',flush=True)
        server.serve_forever()

if __name__ == '__main__':
    main()
