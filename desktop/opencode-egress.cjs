const http=require('node:http');
const net=require('node:net');
const dns=require('node:dns').promises;
const os=require('node:os');
const {publicAddress}=require('./browser.cjs');

async function publicTarget(host,lookup=dns.lookup){
  const local=new Set(Object.values(os.networkInterfaces()).flat().map(item=>item.address));
  const addresses=await lookup(host,{all:true,verbatim:true});
  if(!addresses.length||addresses.some(item=>!publicAddress(item.address)||local.has(item.address)))throw Error('opencode_private_network_denied');
  return addresses[0];
}
function egressServer(){
  const sockets=new Set();
  const server=http.createServer(async(req,res)=>{
    try{
      const url=new URL(req.url);
      if(url.protocol!=='http:'||url.username||url.password||(url.port&&url.port!=='80'))throw Error('denied');
      const target=await publicTarget(url.hostname);
      const headers={...req.headers,host:url.host};delete headers['proxy-authorization'];delete headers['proxy-connection'];
      const upstream=http.request({hostname:target.address,family:target.family,port:80,path:url.pathname+url.search,method:req.method,headers},reply=>{res.writeHead(reply.statusCode,reply.headers);reply.pipe(res);});
      upstream.setTimeout(30000,()=>upstream.destroy());upstream.on('error',()=>{if(!res.headersSent)res.writeHead(502);res.end();});
      req.on('aborted',()=>upstream.destroy());res.on('close',()=>upstream.destroy());req.pipe(upstream);
    }catch{res.writeHead(403);res.end();}
  });
  server.on('connect',async(req,socket,head)=>{
    let upstream;
    try{
      const url=new URL('https://'+req.url);
      if(url.username||url.password||url.pathname!=='/'||url.search||url.hash||url.port)throw Error('denied');
      const target=await publicTarget(url.hostname.replace(/^\[|\]$/g,''));
      if(socket.destroyed)return;
      upstream=net.connect({host:target.address,family:target.family,port:443});
      sockets.add(upstream);upstream.on('close',()=>sockets.delete(upstream));
      upstream.setTimeout(300000,()=>upstream.destroy());
      upstream.once('connect',()=>{socket.write('HTTP/1.1 200 Connection Established\r\n\r\n');if(head.length)upstream.write(head);socket.pipe(upstream);upstream.pipe(socket);});
      upstream.on('error',()=>socket.destroy());socket.on('error',()=>upstream.destroy());socket.on('close',()=>upstream.destroy());
    }catch{socket.end('HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n');upstream?.destroy();}
  });
  server.on('connection',socket=>{sockets.add(socket);socket.on('close',()=>sockets.delete(socket));});
  server.shutdown=()=>{for(const socket of sockets)socket.destroy();server.close();};
  return server;
}
module.exports={publicTarget,egressServer};
