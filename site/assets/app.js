
(function(){
  var tip=document.createElement('div');tip.className='tip';document.body.appendChild(tip);
  function show(e,t){tip.textContent=t;tip.style.opacity=1;move(e)}
  function move(e){var x=e.clientX+14,y=e.clientY+14;var r=tip.getBoundingClientRect();
    if(x+r.width>innerWidth-8)x=e.clientX-r.width-14;if(y+r.height>innerHeight-8)y=e.clientY-r.height-14;
    tip.style.left=x+'px';tip.style.top=y+'px'}
  function hide(){tip.style.opacity=0}
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mouseenter',function(e){show(e,el.getAttribute('data-tip'));
      var svg=el.ownerSVGElement;if(svg&&el.dataset.x){var c=svg.querySelector('.cross'),d=svg.querySelector('.cross-dot');
        c.setAttribute('x1',el.dataset.x);c.setAttribute('x2',el.dataset.x);c.style.opacity=1;d.setAttribute('cx',el.dataset.x);d.setAttribute('cy',el.dataset.y);d.style.opacity=1}});
    el.addEventListener('mousemove',move);
    el.addEventListener('mouseleave',function(){hide();var svg=el.ownerSVGElement;if(svg&&el.dataset.x){svg.querySelector('.cross').style.opacity=0;
      svg.querySelector('.cross-dot').style.opacity=0}});
  });
  document.querySelectorAll('time[datetime]').forEach(function(t){var d=new Date(t.getAttribute('datetime'));
    if(!isNaN(d))t.textContent=d.toLocaleString(undefined,{weekday:'short',day:'numeric',month:'short',hour:'numeric',minute:'2-digit'})});
})();
