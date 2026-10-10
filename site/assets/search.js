
(function(){
  var root=document.getElementById('finder');if(!root)return;
  function $(id){return document.getElementById(id)}
  var q=$('q'),show=$('show'),sort=$('sort'),list=$('results'),summary=$('summary'),more=$('more'),
      headF=$('head-fights'),headE=$('head-events'),tabs=root.querySelectorAll('.tab');
  var PAGE=50,fights=[],events=[],items=[],shown=0,view='fights',timer;
  var day=new Intl.DateTimeFormat(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  function norm(s){return String(s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function pct(p){return Math.round(p*100)+'%'}
  function tier(p){return p>=0.7?'strong':p>=0.6?'lean':'toss'}
  function units(u){return '<b class="'+(u>0?'good':u<0?'bad':'')+'">'+(u>0?'+':'')+u.toFixed(2)+'u</b>'}
  function plural(n,w){return n.toLocaleString()+' '+w+(n===1?'':'s')}
  function when(d){return day.format(new Date(d+'T00:00:00Z'))}

  function load(d){
    events=d.events.map(function(e){return{date:e[0],name:e[1],fights:[],hay:' '+norm(e[1])+' '}});
    fights=d.fights.map(function(r){
      var e=events[r[0]],p=r[5]!=null?r[5]:r[4];
      var f={date:e.date,event:e.name,a:r[1],b:r[2],res:r[3],oa:r[6],ob:r[7],bet:r[8],debut:r[9],
             div:d.divisions[r[10]],method:d.methods[r[11]]};
      if(p!=null){f.side=p>=0.5?'a':'b';f.p=p>=0.5?p:1-p}
      f.winner=f.res===1?'a':f.res===0?'b':null;
      f.right=f.side&&f.winner?f.side===f.winner:null;
      if(f.side&&f.oa&&f.ob){f.odds=f.side==='a'?f.oa:f.ob;f.dog=f.odds>(f.side==='a'?f.ob:f.oa)}
      if(f.bet){f.betOdds=f.bet==='a'?f.oa:f.ob;f.pnl=f.bet===f.winner?f.betOdds-1:-1}
      f.hay=' '+norm(f.a)+' / '+norm(f.b)+' / '+norm(f.event)+' ';
      e.fights.push(f);
      return f;
    }).reverse();
    events.forEach(function(e){
      var picked=0,right=0,conf=0,n=0,bets=0,won=0;
      e.fights.forEach(function(f){
        if(f.p!=null){conf+=f.p;n++}
        if(f.right!=null){picked++;if(f.right)right++}
        if(f.bet){bets++;won+=f.pnl}
      });
      e.picked=picked;e.right=right;e.rate=picked?right/picked:null;e.conf=n?conf/n:null;e.bets=bets;e.won=won;
    });
    events.reverse();
    var params=new URLSearchParams(location.search);
    if(params.get('q'))q.value=params.get('q');
    pick(show,params.get('show'));pick(sort,params.get('sort'));
    setView(params.get('view')==='events'?'events':'fights',false);
  }
  function pick(sel,v){if(v&&sel.querySelector('option[value="'+v+'"]'))sel.value=v}

  function matcher(){
    var words=norm(q.value).split(' ').filter(Boolean),phrase=' '+words.join(' ');
    if(!words.length)return function(){return true};
    // a full name ("Jon Jones") matches as a phrase; otherwise every word can be anywhere ("McGregor Diaz")
    var exact=words.length>1&&fights.some(function(f){return f.hay.indexOf(phrase)>=0});
    return function(hay){
      if(exact)return hay.indexOf(phrase)>=0;
      for(var i=0;i<words.length;i++)if(hay.indexOf(' '+words[i])<0)return false;
      return true;
    };
  }

  function setView(v,remember){
    view=v;
    tabs.forEach(function(t){t.setAttribute('aria-selected',String(t.getAttribute('data-view')===v))});
    headF.hidden=v!=='fights';headE.hidden=v!=='events';show.hidden=v!=='fights';sort.hidden=v!=='events';
    q.placeholder=v==='events'?'Search an event or a fighter on the card':'Search a fighter or event, e.g. Jon Jones';
    run(remember);
  }

  function run(remember){
    var m=matcher(),mode=show.value;
    if(view==='fights'){
      items=fights.filter(function(f){
        if(!m(f.hay))return false;
        if(mode==='bets')return !!f.bet;
        if(mode==='upsets')return f.dog&&f.right;
        if(mode==='misses')return f.right===false;
        return true;
      });
      if(mode==='upsets')items.sort(function(x,y){return y.odds-x.odds});
    }else{
      items=events.filter(function(e){return m(e.hay)||e.fights.some(function(f){return m(f.hay)})});
      var s=sort.value,rated=function(e){return e.rate==null?(s==='best'?-1:2):e.rate};
      if(s==='best')items.sort(function(x,y){return rated(y)-rated(x)||y.picked-x.picked});
      if(s==='worst')items.sort(function(x,y){return rated(x)-rated(y)||y.picked-x.picked});
    }
    shown=0;list.innerHTML='';page();summarise();
    if(remember!==false){
      var url=new URL(location.href),p=url.searchParams;
      q.value.trim()?p.set('q',q.value.trim()):p.delete('q');
      view==='events'?p.set('view','events'):p.delete('view');
      view==='fights'&&mode!=='all'?p.set('show',mode):p.delete('show');
      view==='events'&&sort.value!=='new'?p.set('sort',sort.value):p.delete('sort');
      history.replaceState(null,'',url);
    }
  }

  function summarise(){
    var n=items.length;
    if(!n){summary.innerHTML='Nothing found. Try just a surname, like <b>Holloway</b>.';return}
    if(view==='events'){
      var rated=items.filter(function(e){return e.rate!=null}),avg=0,perfect=0;
      rated.forEach(function(e){avg+=e.rate;if(e.picked>=5&&e.right===e.picked)perfect++});
      summary.innerHTML='<b>'+plural(n,'event')+'</b>'+(rated.length?' · FightIQ picked <b>'+pct(avg/rated.length)
        +'</b> of winners per card on average · '+plural(perfect,'perfect card')+' (5+ fights, every pick right)':'');
      return;
    }
    var picked=0,right=0,bets=0,won=0,mode=show.value;
    items.forEach(function(f){if(f.right!=null){picked++;if(f.right)right++}if(f.bet){bets++;won+=f.pnl}});
    var s='<b>'+plural(n,'fight')+'</b>';
    if(mode==='upsets')s='<b>'+plural(n,'upset')+'</b> FightIQ called: it backed the betting underdog and the underdog won. Biggest odds first';
    else if(mode==='misses')s+=' FightIQ got wrong';
    else if(picked)s+=' · FightIQ picked the winner in <b>'+right.toLocaleString()+' of '+picked.toLocaleString()+'</b> ('+pct(right/picked)+')';
    if(bets)s+=' · '+plural(bets,'bet')+', '+units(won)+' at 1u each';
    summary.innerHTML=s;
  }

  function name(f,side){return side==='a'?f.a:f.b}

  function fightRow(f){
    var meta=when(f.date)+' · '+esc(f.event)+' · '+esc(f.div)+(f.debut?' <span class="tag">Debut</span>':'');
    var pickHtml='<b class="muted">No pick</b><small>nothing earlier to learn from</small>';
    if(f.side){
      pickHtml='<b>'+esc(name(f,f.side))+'</b><small><span class="tier '+tier(f.p)+'">'+pct(f.p)+'</span>'
        +(f.odds?' odds '+f.odds.toFixed(2):'')
        +(f.bet?' <span class="tag accent">Bet '+esc(name(f,f.bet).split(' ').pop())+' @ '+f.betOdds.toFixed(2)+' · '+units(f.pnl)+'</span>':'')
        +'</small>';
    }
    var won=f.winner?'<b>'+esc(name(f,f.winner))+'</b>':'<b>'+(f.res===0.5?'Draw':'No contest')+'</b>';
    var badge=f.right==null?'<span class="res void" title="No pick, draw or no contest">–</span>':
      f.right?'<span class="res win" title="FightIQ was right">✓</span>':'<span class="res loss" title="FightIQ was wrong">✗</span>';
    return '<div class="frow"><span class="who"><b>'+esc(f.a)+' <i>vs</i> '+esc(f.b)+'</b><small>'+meta+'</small></span>'
      +'<span class="who" data-l="FightIQ pick">'+pickHtml+'</span>'
      +'<span class="who" data-l="Winner">'+won+'<small>'+esc(f.method)+'</small></span>'
      +'<span>'+badge+'</span></div>';
  }

  function eventRow(e){
    var r=e.rate,cls=r==null?'toss':r>=0.7?'strong':r>=0.6?'lean':'toss';
    return '<div class="erow"><span class="ev"><button type="button" class="ev-link" data-event="'+esc(e.name)+'">'+esc(e.name)
      +'</button><small>'+when(e.date)+' · '+plural(e.fights.length,'fight')+'</small></span>'
      +'<span data-l="Picks right"><b class="score">'+(e.picked?e.right+'/'+e.picked:'–')+'</b></span>'
      +'<span data-l="Hit rate"><span class="rate"><span class="rate-bar"><span class="'+cls+'" style="width:'+(r==null?0:Math.round(r*100))
      +'%"></span></span><b>'+(r==null?'–':pct(r))+'</b></span></span>'
      +'<span data-l="Avg confidence"><b>'+(e.conf==null?'–':pct(e.conf))+'</b></span>'
      +'<span data-l="Bets">'+(e.bets?plural(e.bets,'bet')+' · '+units(e.won):'<span class="muted">No bets</span>')+'</span></div>';
  }

  function page(){
    list.insertAdjacentHTML('beforeend',items.slice(shown,shown+PAGE).map(view==='events'?eventRow:fightRow).join(''));
    shown=Math.min(shown+PAGE,items.length);
    more.hidden=shown>=items.length;
    if(!more.hidden)more.textContent='Show more ('+(items.length-shown).toLocaleString()+' left)';
  }

  q.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(run,150)});
  show.addEventListener('change',function(){run()});
  sort.addEventListener('change',function(){run()});
  more.addEventListener('click',page);
  tabs.forEach(function(t){t.addEventListener('click',function(){setView(t.getAttribute('data-view'))})});
  list.addEventListener('click',function(e){
    var b=e.target.closest('.ev-link');if(!b)return;
    q.value=b.getAttribute('data-event');show.value='all';setView('fights');root.scrollIntoView({behavior:'smooth'});
  });
  root.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){q.value=c.textContent;run();q.focus()})});
  fetch(root.getAttribute('data-src')).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(load)
    .catch(function(){summary.textContent="Couldn't load the fight data. Please refresh the page."});
})();
