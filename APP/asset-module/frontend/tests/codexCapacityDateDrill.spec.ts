import {mount,flushPromises} from '@vue/test-utils'
import {computed,defineComponent,h,onBeforeUnmount,onMounted,reactive,ref,Suspense,watch} from 'vue'
import {afterEach,expect,it,vi} from 'vitest'
import * as cv from '../composables/capacityView'
import Capacity from '../pages/v2/capacity.vue'
afterEach(()=>vi.unstubAllGlobals())
it('容量矩陣下鑽單機應保留已選分析期間',async()=>{
 for(const[k,v]of Object.entries({...cv,computed,onBeforeUnmount,onMounted,reactive,ref,watch}))vi.stubGlobal(k,v)
 vi.stubGlobal('definePageMeta',()=>{})
 const route=reactive({query:{from:'2026-09-01',to:'2026-09-30',day:'normal',metric:'cpu_busy',stat:'max'}})
 vi.stubGlobal('useRoute',()=>route);vi.stubGlobal('useRouter',()=>({replace:vi.fn()}))
 vi.stubGlobal('useAuth',()=>({isAdmin:ref(false),isViewer:ref(true)}))
 vi.stubGlobal('useToast',()=>({showToast:vi.fn()}));vi.stubGlobal('useGuardedFetch',()=>({guardedFetch:vi.fn()}))
 const calls:any[]=[]
 vi.stubGlobal('useApi',()=>({apiFetch:async(u:string,o:any)=>{calls.push([u,o]);if(u.endsWith('/markers'))return{markers:[],options:{types:[],reasons:[],levels:[],default_ranges:[],server_now:'2026-11-02T07:00:00',today_kind:'pre_announce'}};return{demo:false,other_key:'_other',other_label:'其他',static_limits:[],metrics:[{key:'cpu_busy',label:'CPU忙碌',unit:'%'}],day_types:[],config:{slots:[{key:'open',label:'開盤',start:'09:00',end:'09:30',priority:true}],unprovided:[],slots_source:'DEMO',heavy_days:[],holidays:[],params:{min_coverage_pct:80,min_days:20,target_span_days:90,high_pct:80}},sources:[],hosts:[{host:'DEMO-CAPACITY',no:1,evidence:'limited',evidence_label:'有限制',data_days:20,span_days:30,from:'2026-09-01',to:'2026-09-30',interval_s:60,days_by_type:{normal:20},annotated_days:[],context:{},limits:[],cells:[]}]}}}))
 const Page=defineComponent({setup(_, {slots}){return()=>h('main',[slots.conclusion?.(),slots.tab?.()])}})
 const Table=defineComponent({props:['rows','cols'],setup(p){return()=>h('table',p.rows.map((r:any)=>h('tr',p.cols.map((c:any)=>h('td',c.link?h('a',{href:c.link(r),'data-k':c.k},r.host):'value')))))}})
 const Wrap=defineComponent({setup(){return()=>h(Suspense,null,{default:()=>h(Capacity)})}})
 const w=mount(Wrap,{global:{components:{V2Page:Page,V2Table:Table},stubs:{InfoNote:true,NuxtLink:true}}})
 try{await flushPromises();expect(calls.find(x=>x[0].endsWith('/overview'))[1].query).toEqual({from:'2026-09-01',to:'2026-09-30'});const href=w.find('a[data-k="host"]').attributes('href');expect(href).toBeTruthy();const q=new URL(href!,'http://localhost').searchParams;expect(q.get('host')).toBe('DEMO-CAPACITY');expect(q.get('from')).toBe('2026-09-01');expect(q.get('to')).toBe('2026-09-30')}finally{w.unmount()}
})
