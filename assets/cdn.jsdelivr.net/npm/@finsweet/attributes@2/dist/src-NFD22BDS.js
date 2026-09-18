import{b as E,d as L}from"./chunk-T2PNG2TC.js";import{b as d}from"./chunk-ZZRTGM4V.js";import"./chunk-OF4NI3Y7.js";import"./chunk-N2ZCN6XY.js";import{a as x,b as y,c as I,o as R}from"./chunk-L4B2V5MO.js";import{a as c}from"./chunk-CB6LZ537.js";import{b as l,f as m}from"./chunk-XRTLPMPO.js";import{a as N}from"./chunk-UAA7M346.js";import{j as f}from"./chunk-ZI7TAH2K.js";import{c as g}from"./chunk-GGDEANQW.js";import"./chunk-K46K3RI5.js";var F="1.2.16";var b=(t,e,r)=>{e.setAttribute(x,"button"),e.setAttribute(R,t.id),e.setAttribute(y,"0"),e.hasAttribute(I)||e.setAttribute(I,r)};var h=(t,e,r)=>{for(let n of[e,r].filter(m))b(t,n,`${n===e?"Increment":"Decrement"} the input value`);let o=c(e,"click",n=>{n.preventDefault(),t.stepUp()}),s=c(r,"click",n=>{n.preventDefault(),t.stepDown()});return()=>{o(),s()}},_=(t,e,r)=>(b(t,e,"Reset the input value"),c(e,"click",n=>{n.preventDefault(),d(t,r?.toString()||"")}));var M=t=>{let{step:e,min:r,max:o}=t;if(!e&&!r&&!o)return;let s=E(e),n=E(r),i=E(o);return c(t,"change",()=>{let u=Number(t.value);if(isNaN(u))return;let p=s?L(u,s):u;t.value=l(n)&&p<n?n.toString():l(i)&&p>i?i.toString():p.toString()})};var S=["input","increment","decrement","clear","style"],T={initial:{key:"initial",isNumeric:!0},showarrows:{key:"showarrows"}},H=`<style fs-${f}-element="style">
/* Chrome, Safari, Edge, Opera */
input::-webkit-outer-spin-button,
input::-webkit-inner-spin-button {
  -webkit-appearance: none;
  margin: 0;
}

/* Firefox */
input[type='number'] {
  -moz-appearance: textfield;
}
</style>`;var{queryElement:a,getElementSelector:k,getInstance:v,getAttribute:w}=N(f,S,T);var B=()=>{a("style")||document.head.insertAdjacentHTML("beforeend",H)};var D=t=>{let e=v(t),r=w(t,"showarrows"),o=w(t,"initial"),s=a("increment",{instance:e}),n=a("decrement",{instance:e}),i=a("clear",{instance:e});if(!s&&!n)return;r||B();let A=M(t),u=h(t,s,n),p=i?_(t,i,o):void 0;return t.type="number",l(o)&&d(t,o.toString()),()=>{A?.(),u(),p?.()}};var U=async()=>{await g();let t=[...document.querySelectorAll(`input${k("input")}`)],e=t.map(D).filter(m);return{result:t,destroy(){for(let r of e)r()}}};export{S as ELEMENTS,T as SETTINGS,U as init,F as version};
