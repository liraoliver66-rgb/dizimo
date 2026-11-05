(function(){
const form=document.getElementById('f');if(!form)return;
const MOD=JSON.parse(form.dataset.modelos),H=JSON.parse(form.dataset.hist);
const steps=[...document.querySelectorAll('.step')],ind=[...document.querySelectorAll('.passos li')];
let cur=0;
const brl=c=>'R$ '+(c/100).toLocaleString('pt-BR',{minimumFractionDigits:2,maximumFractionDigits:2});
function show(n){cur=n;steps.forEach((s,i)=>s.classList.toggle('hide',i!==n));ind.forEach((s,i)=>{s.classList.toggle('on',i===n);s.classList.toggle('feito',i<n)});
 if(n===2)resumo();window.scrollTo({top:0,behavior:'smooth'});}
function total(){let t=0;form.querySelectorAll('.qty').forEach(i=>{t+=(parseInt(i.value)||0)*parseInt(i.dataset.preco)});return t}
function upd(){document.querySelectorAll('.tot').forEach(e=>e.textContent=brl(total()))}
function resumo(){const r=document.getElementById('resumo');let h='<table><tr><th>Item</th><th class=num>Qtd</th><th class=num>Subtotal</th></tr>';
 form.querySelectorAll('.qty').forEach(i=>{const q=parseInt(i.value)||0;if(q>0)h+=`<tr><td>${i.dataset.nome}</td><td class=num>${q}</td><td class=num>${brl(q*i.dataset.preco)}</td></tr>`});
 r.innerHTML=h+`<tr><th>Total</th><th></th><th class=num>${brl(total())}</th></tr></table>`}
function hist(){const p=document.getElementById('paroquia').value,b=document.getElementById('hist');
 const h=H[p];if(!h){b.classList.add('hide');return}
 b.classList.remove('hide');b.innerHTML=`<b>O que sua paróquia pediu em 2026:</b> Folder ${h.folder} · Folheto ${h.f1} · Marca-página ${h.mp} · Cartaz P ${h.cp} / G ${h.cg} · Banner ${h.banner}`}
document.querySelectorAll('.qtd').forEach(w=>{const i=w.querySelector('input'),st=parseInt(i.dataset.step)||1;
 w.querySelector('.mn').addEventListener('click',()=>{i.value=Math.max(0,(parseInt(i.value)||0)-st);upd()});
 w.querySelector('.pl').addEventListener('click',()=>{i.value=(parseInt(i.value)||0)+st;upd()})});
form.addEventListener('input',e=>{if(e.target.classList.contains('qty'))upd()});
document.getElementById('paroquia').addEventListener('change',hist);hist();upd();
document.querySelectorAll('[data-modelo]').forEach(b=>b.addEventListener('click',()=>{
 const m=MOD[b.dataset.modelo].q;form.querySelectorAll('.qty').forEach(i=>{if(m[i.dataset.pid]!==undefined)i.value=m[i.dataset.pid]});upd()}));
document.querySelectorAll('[data-limpar]').forEach(b=>b.addEventListener('click',()=>{form.querySelectorAll('.qty').forEach(i=>i.value=0);upd()}));
document.querySelectorAll('[data-go]').forEach(b=>b.addEventListener('click',()=>{
 const n=parseInt(b.dataset.go);
 if(n>0&&cur===0){for(const id of['paroquia','responsavel','whatsapp']){const e=document.getElementById(id);if(!e.value.trim()){e.focus();alert('Preencha: '+e.dataset.rot);return}}}
 if(n===2&&total()===0){alert('Escolha ao menos um item.');return}show(n)}));
show(0);
})();
