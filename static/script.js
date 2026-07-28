
async function load(){
 let r=await fetch('/api/tickets'); let d=await r.json();
 document.getElementById('list').innerHTML=d.map(t=>`<p><b>${t.priority}</b> - ${t.ticket_text}</p>`).join('');
}
async function send(){
 let r=await fetch('/api/classify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:ticket.value,user_id:user.value})});
 let d=await r.json();
 out.textContent=JSON.stringify(d,null,2);
 load();
}
load();
