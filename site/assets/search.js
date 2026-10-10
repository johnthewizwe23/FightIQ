
(function(){
  var root=document.getElementById('finder');if(!root)return;
  var q=document.getElementById('q'),show=document.getElementById('show'),list=document.getElementById('results'),
      summary=document.getElementById('summary'),more=document.getElementById('more');
  var PAGE=50,fights=[],matches=[],shown=0,timer;
  var day=new Intl.DateTimeFormat(undefined,{day:'numeric',month:'short',year:'numeric',timeZone:'UTC'});
  function norm(s){return String(s).normalize('NFD').replace(/[̀-ͯ]/g,'').toLowerCase().replace(/[^a-z0-9]+/g,' ').trim()}
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]})}
  function pct(p){return Math.round(p*100)+'%'}
  function tier(p){return p>=0.7?'strong':p>=0.6?'lean':'toss'}
  function units(u){return '<b class="'+(u>0?'good':u<0?'bad':'')+'">'+(u>0?'+':'')+u.toFixed(2)+'u</b>'}

  function load(d){
    fights=d.fights.map(function(r){
      var e=d.events[r[0]],p=r[5]!=null?r[5]:r[4];
      var f={date:e[0],event:e[1],a:r[1],b:r[2],res:r[3],oa:r[6],ob:r[7],bet:r[8],debut:r[9],
             div:d.divisions[r[10]],method:d.methods[r[11]]};
      if(p!=null){f.side=p>=0.5?'a':'b';f.p=p>=0.5?p:1-p}
      f.winner=f.res===1?'a':f.res===0?'b':null;
      f.right=f.side&&f.winner?f.side===f.winner:null;
      if(f.side&&f.oa&&f.ob){f.odds=f.side==='a'?f.oa:f.ob;f.dog=f.odds>(f.side==='a'?f.ob:f.oa)}
      if(f.bet){f.betOdds=f.bet==='a'?f.oa:f.ob;f.pnl=f.bet===f.winner?f.betOdds-1:-1}
      f.hay=' '+norm(f.a)+' / '+norm(f.b)+' / '+norm(f.event)+' ';
      return f;
    }).reverse();
    var params=new URLSearchParams(location.search);
    if(params.get('q'))q.value=params.get('q');
    if(params.get('show')&&show.querySelector('option[value="'+params.get('show')+'"]'))show.value=params.get('show');
    run(false);
  }

  function run(remember){
    var words=norm(q.value).split(' ').filter(Boolean),mode=show.value,phrase=' '+words.join(' ');
    // a full name ("Jon Jones") matches as a phrase; otherwise every word can be anywhere ("McGregor Diaz")
    var exact=words.length>1&&fights.some(function(f){return f.hay.indexOf(phrase)>=0});
    matches=fights.filter(function(f){
      if(exact){if(f.hay.indexOf(phrase)<0)return false}
      else for(var i=0;i<words.length;i++)if(f.hay.indexOf(' '+words[i])<0)return false;
      if(mode==='bets')return !!f.bet;
      if(mode==='upsets')return f.dog&&f.right;
      if(mode==='misses')return f.right===false;
      return true;
    });
    if(mode==='upsets')matches.sort(function(x,y){return y.odds-x.odds});
    shown=0;list.innerHTML='';page();summarise();
    if(remember!==false){
      var url=new URL(location.href);
      words.length?url.searchParams.set('q',q.value.trim()):url.searchParams.delete('q');
      mode!=='all'?url.searchParams.set('show',mode):url.searchParams.delete('show');
      history.replaceState(null,'',url);
    }
  }

  function summarise(){
    var n=matches.length,picked=0,right=0,bets=0,won=0;
    matches.forEach(function(f){if(f.right!=null){picked++;if(f.right)right++}if(f.bet){bets++;won+=f.pnl}});
    if(!n){summary.innerHTML='No fights found. Try just a surname, like <b>Holloway</b>.';return}
    var mode=show.value,s='<b>'+n.toLocaleString()+'</b> fight'+(n===1?'':'s');
    if(mode==='upsets')s='<b>'+n.toLocaleString()+'</b> upset'+(n===1?'':'s')+' FightIQ called: it backed the betting underdog and the underdog won. Biggest odds first';
    else if(mode==='misses')s+=' FightIQ got wrong';
    else if(picked)s+=' · FightIQ picked the winner in <b>'+right.toLocaleString()+' of '+picked.toLocaleString()+'</b> ('+pct(right/picked)+')';
    if(bets)s+=' · '+bets+' bet'+(bets===1?'':'s')+', '+units(won)+' at 1u each';
    summary.innerHTML=s;
  }

  function name(f,side){return side==='a'?f.a:f.b}

  function row(f){
    var meta=day.format(new Date(f.date+'T00:00:00Z'))+' · '+esc(f.event)+' · '+esc(f.div)+(f.debut?' <span class="tag">Debut</span>':'');
    var pick='<b class="muted">No pick</b><small>nothing earlier to learn from</small>';
    if(f.side){
      pick='<b>'+esc(name(f,f.side))+'</b><small><span class="tier '+tier(f.p)+'">'+pct(f.p)+'</span>'
          +(f.odds?' odds '+f.odds.toFixed(2):'')
          +(f.bet?' <span class="tag accent">Bet '+esc(name(f,f.bet).split(' ').pop())+' @ '+f.betOdds.toFixed(2)+' · '+units(f.pnl)+'</span>':'')
          +'</small>';
    }
    var won=f.winner?'<b>'+esc(name(f,f.winner))+'</b>':'<b>'+(f.res===0.5?'Draw':'No contest')+'</b>';
    var badge=f.right==null?'<span class="res void" title="No pick, draw or no contest">–</span>':
      f.right?'<span class="res win" title="FightIQ was right">✓</span>':'<span class="res loss" title="FightIQ was wrong">✗</span>';
    return '<div class="frow"><span class="who"><b>'+esc(f.a)+' <i>vs</i> '+esc(f.b)+'</b><small>'+meta+'</small></span>'
      +'<span class="who" data-l="FightIQ pick">'+pick+'</span>'
      +'<span class="who" data-l="Winner">'+won+'<small>'+esc(f.method)+'</small></span>'
      +'<span>'+badge+'</span></div>';
  }

  function page(){
    list.insertAdjacentHTML('beforeend',matches.slice(shown,shown+PAGE).map(row).join(''));
    shown=Math.min(shown+PAGE,matches.length);
    more.hidden=shown>=matches.length;
    if(!more.hidden)more.textContent='Show more ('+(matches.length-shown).toLocaleString()+' left)';
  }

  q.addEventListener('input',function(){clearTimeout(timer);timer=setTimeout(run,150)});
  show.addEventListener('change',function(){run()});
  more.addEventListener('click',page);
  root.querySelectorAll('.chip').forEach(function(c){c.addEventListener('click',function(){q.value=c.textContent;run();q.focus()})});
  fetch(root.getAttribute('data-src')).then(function(r){if(!r.ok)throw new Error(r.status);return r.json()}).then(load)
    .catch(function(){summary.textContent="Couldn't load the fight data. Please refresh the page."});
})();
