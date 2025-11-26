// Simple cart using localStorage
function getCart(){ return JSON.parse(localStorage.getItem('cart') || '[]'); }
function saveCart(c){ localStorage.setItem('cart', JSON.stringify(c)); document.getElementById('cart-count').textContent = c.reduce((s,i)=>s+i.quantity,0); }
window.addEventListener('DOMContentLoaded', ()=>{
  const buttons = document.querySelectorAll('.add-to-cart');
  buttons.forEach(btn=>{
    btn.addEventListener('click', ()=>{
      const id = parseInt(btn.dataset.id);
      const title = btn.dataset.title;
      const price = parseFloat(btn.dataset.price);
      let cart = getCart();
      let item = cart.find(i=>i.product_id===id);
      if(item){ item.quantity += 1; } else { cart.push({product_id:id,title,price,quantity:1}); }
      saveCart(cart);
      alert('Produit ajouté au panier');
    });
  });
  if(document.getElementById('cart-count')) saveCart(getCart());
});
async function checkout(address){
  const cart = getCart();
  const resp = await fetch('/api/checkout',{
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({address, items: cart})
  });
  const j = await resp.json();
  if(j.ok){ localStorage.removeItem('cart'); alert('Commande passée! ID: '+j.order_id); window.location.href='/'; }
  else if(j.error=='not_authenticated'){ alert('Veuillez vous connecter'); window.location.href='/login'; }
  else if(j.error=='insufficient_stock'){ alert('Stock insuffisant pour un produit'); }
}
