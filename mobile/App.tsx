import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, AppState, KeyboardAvoidingView, Platform, Pressable, RefreshControl, ScrollView, StyleSheet, Text, TextInput, View } from 'react-native';
import { router } from 'expo-router';
import { SafeAreaProvider, SafeAreaView } from 'react-native-safe-area-context';
import { StatusBar } from 'expo-status-bar';
import { ApiError, Home, Item, Kind, loadSession, request, saveSession, serverUrl, Session } from './src/api';
import { demoHome, demoItems } from './src/demo';

const names: Record<string,string> = { home:'Overview',classes:'Classes',plans:'My programme',account:'Membership',leads:'My leads',members:'Member directory',queries:'My queries',notifications:'Notifications',hr:'My timesheets' };
const icons: Record<string,string> = { classes:'↗',plans:'≡',account:'◎',leads:'↗',members:'◎',queries:'≡',notifications:'◉',hr:'◷' };
const descriptions: Record<string,string> = { classes:'Find a session that fits your week.',plans:'Your next step starts here.',account:'Your membership, at a glance.',leads:'Your assigned prospects and next actions.',members:'Browse recent member records.',queries:'Your assigned member enquiries.',notifications:'Work that needs your attention.',hr:'Your recent weekly time records.' };
const c={ink:'#152E2B',muted:'#667873',lime:'#D8F15A',paper:'#F6F7F2',line:'#DFE5DA',white:'#FFFFFF'};
function Button({title,onPress,secondary=false,disabled=false}:{title:string;onPress:()=>void;secondary?:boolean;disabled?:boolean}) {
  return <Pressable accessibilityRole="button" onPress={onPress} disabled={disabled} style={({pressed})=>[s.button,secondary&&s.secondary,(disabled||pressed)&&{opacity:.5}]}><Text style={s.buttonText}>{title}</Text></Pressable>;
}
function Field({label,value,onChange,placeholder,secure=false,numeric=false,editable=true}:{label:string;value:string;onChange:(v:string)=>void;placeholder?:string;secure?:boolean;numeric?:boolean;editable?:boolean}) {
  return <View style={s.field}><Text style={s.label}>{label}</Text><TextInput accessibilityLabel={label} value={value} onChangeText={onChange} placeholder={placeholder} placeholderTextColor={c.muted} secureTextEntry={secure} autoCapitalize="none" autoCorrect={false} editable={editable} keyboardType={numeric?'number-pad':label.includes('Email')?'email-address':'default'} maxLength={numeric?6:undefined} style={s.input}/></View>;
}
function Application() {
  const [session,setSession]=useState<Session|null>(null),[starting,setStarting]=useState(true),[kind,setKind]=useState<Kind>('staff');
  const [base,setBase]=useState(process.env.EXPO_PUBLIC_API_URL||'https://www.1cpace.co.za'),[gym,setGym]=useState(process.env.EXPO_PUBLIC_TENANT||'elev8');
  const [connectionOpen,setConnectionOpen]=useState(false);
  const [email,setEmail]=useState(''),[reference,setReference]=useState('');
  const [challenge,setChallenge]=useState(''),[code,setCode]=useState(''),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [home,setHome]=useState<Home|null>(null),[tab,setTab]=useState('home'),[rows,setRows]=useState<Item[]>([]),[query,setQuery]=useState('');
  const [notice,setNotice]=useState(''),[refreshing,setRefreshing]=useState(false); const version=useRef(0),requestId=useRef(0);
  useEffect(()=>{loadSession().then(async saved=>{
    if(saved?.kind==='staff') {
      await saveSession(null);
      request(saved,'/auth/logout',{}).catch(()=>{});
      router.replace('/workspace');
    } else setSession(saved);
  }).catch(()=>setError('Saved sign-in could not be restored.')).finally(()=>setStarting(false));},[]);
  async function signOut() {
    version.current++;requestId.current++;const old=session;
    setSession(null);setHome(null);setRows([]);setTab('home');setChallenge('');setCode('');setError('');setNotice('');setRefreshing(false);
    await saveSession(null).catch(()=>setError('Saved sign-in could not be cleared. Please try signing out again.'));
    if(old&&!old.demo)request(old,'/auth/logout',{}).catch(()=>{});
  }
  async function reload(next=tab) {
    if(!session)return;const v=version.current,id=++requestId.current;setRefreshing(true);setError('');
    try {
      const info=session.demo?demoHome(session.kind):await request<Home>(session,'/home');
      if(v!==version.current||id!==requestId.current)return;setHome(info);
      if(next!=='home') {
        if(!info.features.includes(next)){setTab('home');setRows([]);return;}
        const path=next==='classes'?'/classes':`/${session.kind}/${next}`;
        const result=session.demo?{items:demoItems[next]||[]}:await request<{items:Item[]}>(session,path);
        if(v===version.current&&id===requestId.current)setRows(result.items);
      }
    }catch(e){
      if(v!==version.current||id!==requestId.current)return;
      if(e instanceof ApiError&&e.status===401){await signOut();setError('Session expired. Please sign in again.');}
      else setError(e instanceof Error?e.message:'Could not load your information.');
    }finally{if(v===version.current&&id===requestId.current)setRefreshing(false);}
  }
  useEffect(()=>{setRows([]);setQuery('');setNotice('');reload(tab);},[session,tab]);
  useEffect(()=>{const sub=AppState.addEventListener('change',state=>{if(state==='active'&&session)reload();});return ()=>sub.remove();},[session,tab]);
  async function submit() {
    if(busy)return;if(kind==='staff'){openStaffWebsite();return;}setBusy(true);setError('');
    try {
      const target={base:serverUrl(base),gym:gym.trim()};if(!target.gym)throw new Error('Enter your gym code.');
      if(!challenge){const response=await request<{challenge:string}>(target,'/auth/start',{kind,email,reference});setChallenge(response.challenge);}
      else{const result=await request<{token:string;kind:Kind}>(target,'/auth/verify',{challenge,code});const next={...target,...result};await saveSession(next);setTab('home');setSession(next);setChallenge('');setCode('');}
    }catch(e){setError(e instanceof Error?e.message:'Unable to sign in.');}finally{setBusy(false);}
  }
  function preview(){version.current++;setError('');setTab('home');setSession({base:'',gym:'demo',token:'',kind,demo:true});}
  function openStaffWebsite(){router.push('/workspace');}
  function choose(next:string){if(next===tab)return;requestId.current++;setError('');setTab(next);}
  async function booking(item:Item) {
    if(!session||busy)return;setBusy(true);setError('');setNotice('');
    try {
      if(session.demo){setRows(old=>old.map(r=>r.id===item.id?{...r,booked:!r.booked,status:r.booked?'Available':'Booked'}:r));setNotice('Preview updated. No real booking was made.');}
      else{await request(session,`/classes/${item.id}/booking`,{action:item.booked?'cancel':'book'});await reload('classes');setNotice(item.booked?'Booking cancelled.':'You are booked in.');}
    }catch(e){if(e instanceof ApiError&&e.status===401){await signOut();setError('Session expired. Sign in again.');}else setError(e instanceof Error?e.message:'Could not update booking.');}
    finally{setBusy(false);}
  }
  const filtered=rows.filter(r=>`${r.title} ${r.subtitle} ${r.detail||''}`.toLowerCase().includes(query.toLowerCase()));
  if(starting)return <View style={s.loading}><ActivityIndicator color={c.ink}/></View>;
  return <SafeAreaView style={s.safe} edges={['top','bottom']}><StatusBar style="dark"/><KeyboardAvoidingView style={{flex:1}} behavior={Platform.OS==='ios'?'padding':undefined}>
    {!session?<ScrollView contentContainerStyle={s.login} keyboardShouldPersistTaps="handled">
      <View style={s.wordmark}><View style={s.mark}><Text style={s.markText}>1C</Text></View><Text style={s.brand}>1Cpace<Text style={s.dot}>.</Text></Text></View>
      <Text style={s.eyebrow}>YOUR GYM. IN YOUR POCKET.</Text><Text style={s.loginTitle}>{kind==='member'?'Make room\nfor progress.':'A clearer view.\nA better workday.'}</Text>
      <Text style={s.intro}>{kind==='member'?'Your sessions, your programme, your membership. All in one place.':'Stay close to your members and the work that matters.'}</Text>
      <View style={s.segment}>{(['member','staff'] as Kind[]).map(value=><Pressable key={value} accessibilityRole="tab" accessibilityState={{selected:kind===value}} disabled={busy||!!challenge} onPress={()=>{setKind(value);setError('');}} style={[s.segmentItem,kind===value&&s.segmentActive]}><Text style={[s.segmentText,kind===value&&{color:c.white}]}>{value==='member'?'I’m a member':'I’m staff'}</Text></Pressable>)}</View>
      {kind==='staff'&&!challenge&&<View style={s.form}><Text style={s.cardTitle}>Your complete staff workspace</Text><Text style={s.body}>Use the full gym system: members, sales, collections, DebitCheck, HR, fitness, reports and administration. Your account permissions determine which features are available.</Text><Button title="Open full staff workspace" onPress={openStaffWebsite}/><Text style={s.body}>Sign in with your usual staff account and email code.</Text></View>}
      {kind==='member'&&<View style={s.form}>{challenge?<><Text style={s.cardTitle}>Check your email</Text><Text style={s.body}>Check the email registered with your gym for a six-digit code. If nothing arrives, confirm your details with the gym. Codes expire in five minutes.</Text><Field label="Sign-in code" value={code} onChange={setCode} numeric editable={!busy}/></>:
        <><Text style={s.label}>{process.env.EXPO_PUBLIC_GYM_NAME||'Elev8 Health and Fitness Gym'}</Text>
        {__DEV__&&<Button title={connectionOpen?'Hide development connection':'Development connection settings'} secondary disabled={busy} onPress={()=>setConnectionOpen(!connectionOpen)}/>}
        {__DEV__&&connectionOpen&&<><Field label="Gym server" value={base} onChange={setBase} placeholder="https://your-gym.example" editable={!busy}/><Field label="Gym code" value={gym} onChange={setGym} editable={!busy}/></>}
        <Field label="Membership reference" value={reference} onChange={setReference} editable={!busy}/><Field label="Email on your membership" value={email} onChange={setEmail} editable={!busy}/></>}
        {!!error&&<Text accessibilityRole="alert" style={s.error}>{error}</Text>}<Button title={busy?'Please wait…':challenge?'Verify & continue':'Send sign-in code  →'} disabled={busy||(!!challenge&&!/^\d{6}$/.test(code))} onPress={submit}/>
        {challenge&&<Button title="Start again" secondary disabled={busy} onPress={()=>{setChallenge('');setCode('');setError('');}}/>}
      </View>}{!challenge&&<Button title="Explore a fictional preview" secondary disabled={busy} onPress={preview}/>}
      <Text style={s.footnote}>Use the details registered with your gym. Ask reception if you need your membership reference or help signing in.</Text>
    </ScrollView>:<>
      <View style={s.topbar}><Pressable accessibilityRole="button" accessibilityLabel="Return to overview" onPress={()=>choose('home')}><Text style={s.brandSmall}>1Cpace<Text style={s.dot}>.</Text></Text></Pressable><View style={s.topRight}><Text style={s.role}>{session.kind.toUpperCase()}</Text><Pressable accessibilityRole="button" onPress={()=>{signOut().catch(()=>setError('Could not clear saved sign-in.'));}}><Text style={s.signOut}>Sign out</Text></Pressable></View></View>
      {session.demo&&<View style={s.demo}><Text style={s.demoText}>FICTIONAL PREVIEW · No live gym data</Text></View>}
      <ScrollView contentContainerStyle={s.content} keyboardShouldPersistTaps="handled" refreshControl={<RefreshControl refreshing={refreshing} onRefresh={()=>reload()}/>}>
        <Text style={s.eyebrow}>{home?.gym||'YOUR GYM'} / {names[tab]?.toUpperCase()}</Text><Text style={s.pageTitle}>{tab==='home'?`Hello, ${home?.name.split(' ')[0]||'there'}.`:names[tab]}</Text><Text style={s.intro}>{tab==='home'?(session.kind==='member'?'Let’s keep moving forward.':'Here’s where your day begins.'):descriptions[tab]}</Text>
        {!!error&&<View style={s.errorBox}><Text accessibilityRole="alert" style={s.error}>{error}</Text><Button title="Try again" secondary onPress={()=>reload()}/></View>}{!!notice&&<Text accessibilityRole="alert" style={s.notice}>{notice}</Text>}
        {tab==='home'?<>
          <View style={s.hero}><Text style={s.heroOverline}>{session.kind==='member'?'YOUR MEMBERSHIP':'YOUR WORKSPACE'}</Text><Text style={s.heroTitle}>{session.kind==='member'?(home?.package||'Your next chapter'):(home?.department||'Your team')}</Text><View style={s.heroLine}><Text style={s.heroSub}>{session.kind==='member'?home?.reference:'Connected to your gym'}</Text><Text style={s.status}>{session.kind==='member'?(home?.membership||'Loading'):'Staff access'}</Text></View><View style={s.heroRule}/><Text style={s.heroSub}>{session.kind==='member'?'Consistency is where progress begins.':'Your permissions determine what you can see.'}</Text></View>
          <View style={s.sectionHeading}><Text style={s.sectionTitle}>Your essentials</Text><Text style={s.smallMuted}>ONE PLACE. LESS FRICTION.</Text></View><View style={s.grid}>{home?.features.map((name,index)=><Pressable key={name} accessibilityRole="button" onPress={()=>choose(name)} style={({pressed})=>[s.tile,index===0&&s.tileFeatured,pressed&&{opacity:.65}]}><View style={s.tileTop}><Text style={s.tileIcon}>{icons[name]}</Text><Text style={s.tileArrow}>↗</Text></View><Text style={s.tileTitle}>{names[name]}</Text><Text style={s.tileBody}>{descriptions[name]}</Text></Pressable>)}</View>
          <View style={s.note}><Text style={s.cardTitle}>{session.kind==='member'?'A little progress, every day.':'Keep the full picture.'}</Text><Text style={s.body}>{session.kind==='member'?'Speak to your trainer when you need a hand. Your programme is a starting point for the conversation.':'This first mobile release focuses on your work lists. Approvals, detailed actions and payroll remain in the staff website.'}</Text>{session.kind==='staff'&&!session.demo&&<Button title="Open the full staff website" secondary onPress={openStaffWebsite}/>}</View>
        </>:<><TextInput accessibilityLabel="Filter loaded records" style={s.search} value={query} onChangeText={setQuery} placeholder="Filter this list…" placeholderTextColor={c.muted}/><Text style={s.smallMuted}>{filtered.length} RECORDS · PULL TO REFRESH</Text>
          {!refreshing&&!error&&!filtered.length&&<View style={s.empty}><Text style={s.cardTitle}>Nothing here yet.</Text><Text style={s.body}>{query?'Try another search.':'New records appear when your gym adds them.'}</Text></View>}
          {filtered.map(item=><View key={item.id} style={s.record}><Text style={s.recordTitle}>{item.title}</Text><Text style={s.badge}>{item.status.replaceAll('_',' ')}</Text><Text style={s.recordSub}>{item.subtitle}</Text>{!!item.detail&&<Text style={s.body}>{item.detail}</Text>}{tab==='classes'&&session.kind==='member'&&<Button title={busy?'Updating…':item.booked?'Cancel booking':'Book this class  →'} secondary={!!item.booked} disabled={busy} onPress={()=>booking(item)}/>}</View>)}
          {['leads','members','queries'].includes(tab)&&<Text style={s.footnote}>Up to 50 records. Leads and queries are assigned to you. Use the staff website for full searches and case actions.</Text>}
        </>}<Text style={s.footer}>1CPACE / MOVE FORWARD, TOGETHER.</Text>
      </ScrollView><View style={s.bottom}><Pressable accessibilityRole="tab" accessibilityState={{selected:tab==='home'}} onPress={()=>choose('home')} style={s.navItem}><Text style={[s.navIcon,tab==='home'&&s.navSelected]}>⌂</Text><Text style={s.navText}>Overview</Text></Pressable>{home?.features.slice(0,3).map(name=><Pressable key={name} accessibilityRole="tab" accessibilityState={{selected:tab===name}} onPress={()=>choose(name)} style={s.navItem}><Text style={[s.navIcon,tab===name&&s.navSelected]}>{icons[name]}</Text><Text style={s.navText}>{name==='plans'?'Programme':name==='account'?'Membership':names[name].replace('My ','')}</Text></Pressable>)}</View>
    </>}
  </KeyboardAvoidingView></SafeAreaView>;
}
export default function App(){return <SafeAreaProvider><Application/></SafeAreaProvider>;}
const s=StyleSheet.create({
  safe:{flex:1,backgroundColor:c.paper},loading:{flex:1,alignItems:'center',justifyContent:'center',backgroundColor:c.paper},login:{padding:26,paddingTop:35,gap:22,width:'100%',maxWidth:560,alignSelf:'center'},wordmark:{flexDirection:'row',alignItems:'center',gap:10,marginBottom:16},mark:{width:36,height:36,backgroundColor:c.ink,borderRadius:11,alignItems:'center',justifyContent:'center'},markText:{color:c.lime,fontWeight:'900',fontSize:16},brand:{fontSize:29,fontWeight:'800',letterSpacing:-1.5,color:c.ink},dot:{color:'#96AD24'},brandSmall:{fontSize:26,fontWeight:'800',letterSpacing:-1,color:c.ink},
  eyebrow:{fontSize:10,fontWeight:'700',letterSpacing:2,color:c.muted},loginTitle:{fontSize:46,lineHeight:49,fontWeight:'800',letterSpacing:-2.2,color:c.ink},intro:{fontSize:15,lineHeight:23,color:c.muted,maxWidth:400},segment:{flexDirection:'row',padding:5,backgroundColor:'#E9EDE3',borderRadius:15},segmentItem:{flex:1,padding:14,borderRadius:11,alignItems:'center'},segmentActive:{backgroundColor:c.ink},segmentText:{fontSize:14,fontWeight:'600',color:c.muted},form:{gap:17},field:{gap:8},label:{fontSize:12,fontWeight:'700',color:c.ink},input:{borderWidth:1,borderColor:c.line,borderRadius:12,padding:15,fontSize:16,color:c.ink,backgroundColor:c.white,minHeight:51},
  button:{minHeight:49,padding:14,borderRadius:12,alignItems:'center',justifyContent:'center',backgroundColor:c.lime,marginTop:4},secondary:{backgroundColor:'transparent',borderColor:c.line,borderWidth:1},buttonText:{color:c.ink,fontSize:14,fontWeight:'700'},footnote:{fontSize:12,lineHeight:19,color:c.muted},error:{fontSize:13,lineHeight:20,color:'#A33127'},errorBox:{backgroundColor:'#FFF0EB',padding:15,borderRadius:12,gap:10},notice:{fontSize:13,lineHeight:21,color:'#30603A',padding:12,backgroundColor:'#E9F1DD',borderRadius:10},
  topbar:{paddingHorizontal:24,paddingVertical:18,flexDirection:'row',justifyContent:'space-between',alignItems:'center',borderBottomWidth:1,borderBottomColor:c.line},topRight:{flexDirection:'row',gap:16,alignItems:'center'},role:{fontSize:9,fontWeight:'800',letterSpacing:1,color:c.ink,backgroundColor:'#E7ECD9',padding:7,borderRadius:5},signOut:{fontSize:12,color:c.muted,paddingVertical:10},demo:{backgroundColor:'#EAF0C8',padding:8,alignItems:'center'},demoText:{fontSize:10,fontWeight:'700',letterSpacing:.7,color:c.ink},content:{padding:24,gap:17,width:'100%',maxWidth:760,alignSelf:'center'},pageTitle:{fontSize:35,fontWeight:'800',letterSpacing:-1.4,color:c.ink,marginTop:7},
  hero:{backgroundColor:c.ink,padding:24,borderRadius:22,gap:17,marginTop:8},heroOverline:{fontSize:10,fontWeight:'600',letterSpacing:2,color:'#CFDBBB'},heroTitle:{fontSize:26,fontWeight:'600',letterSpacing:-.6,color:c.white},heroLine:{flexDirection:'row',justifyContent:'space-between',gap:8,alignItems:'center',flexWrap:'wrap'},heroSub:{color:'#B9CBC3',fontSize:12,lineHeight:19},heroRule:{height:1,backgroundColor:'#3B514A'},status:{backgroundColor:c.lime,paddingHorizontal:11,paddingVertical:6,borderRadius:20,fontSize:10,fontWeight:'700',color:c.ink},sectionHeading:{flexDirection:'row',justifyContent:'space-between',alignItems:'center',gap:8,flexWrap:'wrap',marginTop:15},sectionTitle:{fontSize:19,fontWeight:'700',letterSpacing:-.5,color:c.ink},smallMuted:{fontSize:9,letterSpacing:1,color:c.muted},grid:{flexDirection:'row',flexWrap:'wrap',gap:12},tile:{width:'47%',flexGrow:1,borderRadius:18,padding:18,backgroundColor:c.white,borderColor:c.line,borderWidth:1,minHeight:166,gap:11},tileFeatured:{backgroundColor:c.lime,borderColor:c.lime},tileTop:{flexDirection:'row',alignItems:'center',justifyContent:'space-between',marginBottom:8},tileIcon:{fontSize:26,color:c.ink},tileArrow:{fontSize:20,color:c.ink},tileTitle:{fontSize:17,fontWeight:'700',letterSpacing:-.4,color:c.ink},tileBody:{fontSize:11,lineHeight:17,color:'#54665A'},
  note:{padding:20,borderLeftWidth:3,borderLeftColor:c.lime,backgroundColor:'#EDF0E6',gap:8,borderRadius:4,marginTop:10},cardTitle:{fontSize:18,fontWeight:'600',color:c.ink},body:{fontSize:13,lineHeight:21,color:c.muted},footer:{fontSize:9,letterSpacing:1.5,color:c.muted,marginTop:25,marginBottom:10},bottom:{flexDirection:'row',justifyContent:'space-around',paddingVertical:10,borderTopWidth:1,borderTopColor:c.line,backgroundColor:c.paper},navItem:{alignItems:'center',padding:6,gap:4,flex:1},navIcon:{fontSize:21,color:c.muted,paddingHorizontal:15,borderRadius:12},navSelected:{color:c.ink,backgroundColor:c.lime},navText:{fontSize:10,color:c.ink},search:{backgroundColor:c.white,borderColor:c.line,borderWidth:1,borderRadius:12,padding:15,fontSize:15,color:c.ink,marginTop:6},record:{padding:20,borderRadius:17,backgroundColor:c.white,borderWidth:1,borderColor:c.line,gap:10},recordTitle:{fontSize:18,fontWeight:'600',color:c.ink},recordSub:{fontSize:12,color:c.muted},badge:{alignSelf:'flex-start',fontSize:10,color:c.ink,backgroundColor:'#ECF1DE',paddingVertical:5,paddingHorizontal:9,borderRadius:6,textTransform:'capitalize'},empty:{paddingVertical:40,gap:10},
});
