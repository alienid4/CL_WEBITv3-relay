import {mount,flushPromises} from '@vue/test-utils'
import {computed,defineComponent,h,onBeforeUnmount,onMounted,reactive,ref,Suspense,watch} from 'vue'
import {afterEach,expect,it,vi} from 'vitest'
import * as cv from '../composables/capacityView'
import Capacity from '../pages/v2/capacity.vue'
afterEach(()=>vi.unstubAllGlobals())
it('離開示範且真來源失敗不得移除仍顯示資料的示範標示',async()=>{
 for(const[k,v]of Object.entries({...cv,computed,onBeforeUnmount,onMounted,reactive,ref,watch}))vi.stubGlobal(k,v)
 vi.stubGlobal('definePageMeta',()=>{})
 const route=reactive<{query:Record<string,string>}>({query:{demo:'1',day:'normal',metric:'cpu_busy',stat:'max'}})
 vi.stubGlobal('useRoute',()=>route);vi.stubGlobal('useRouter',()=>({replace:(o:any)=>{route.query=Object.fromEntries(Object.entries(o.query).filter(([,v])=>v!==undefined)) as Record<string,string>}}))
 vi.stubGlobal('useAuth',()=>({isAdmin:ref(false),isViewer:ref(true)}))
 vi.stubGlobal('useToast',()=>({showToast:vi.fn()}));vi.stubGlobal('useGuardedFetch',()=>({guardedFetch:vi.fn()}))
 const calls:any[]=[]
 vi.stubGlobal('useApi',()=>({apiFetch:async(u:string,o:any)=>{calls.push([u,o]);if(u.endsWith('/overview'))throw new Error('DEMO real source unavailable');if(u.endsWith('/markers'))return{markers:[],options:{types:[],reasons:[],levels:[],default_ranges:[],server_now:'2026-11-02T07:00:00',today_kind:'pre_announce'}};return{demo:false,other_key:'_other',other_label:'其他',static_limits:[],metrics:[{key:'cpu_busy',label:'CPU忙碌',unit:'%'}],day_types:[],config:{slots:[{key:'open',label:'開盤',start:'09:00',end:'09:30',priority:true}],unprovided:[],slots_source:'DEMO',heavy_days:[],holidays:[],params:{min_coverage_pct:80,min_days:20,target_span_days:90,high_pct:80}},sources:[],hosts:[{host:'DEMO-CAPACITY',no:1,evidence:'limited',evidence_label:'有限制',data_days:20,span_days:30,from:'2026-09-01',to:'2026-09-30',interval_s:60,days_by_type:{normal:20},annotated_days:[],context:{},limits:[],cells:[]}]}}}))
 const Page=defineComponent({setup(_, {slots}){return()=>h('main',[slots.title?.(),slots.conclusion?.(),slots.tab?.()])}})
 const Table=defineComponent({props:['rows','cols'],setup(p){return()=>h('table',p.rows.map((r:any)=>h('tr',p.cols.map((c:any)=>h('td',c.link?h('a',{href:c.link(r),'data-k':c.k},r.host):'value')))))}})
 const Wrap=defineComponent({setup(){return()=>h(Suspense,null,{default:()=>h(Capacity)})}})
 const w=mount(Wrap,{global:{components:{V2Page:Page,V2Table:Table},stubs:{InfoNote:true,NuxtLink:true}}})
 try{
   await flushPromises()
   expect(w.find('.demo').exists()).toBe(true)
   await w.findAll('button').find(b=>b.text()==='離開示範')!.trigger('click')
   await flushPromises()
   expect(calls.some(x=>x[0].endsWith('/overview'))).toBe(true)
   expect(w.text()).toContain('容量分析載入失敗')
   // Clearing demo data or retaining a visible demo badge are both acceptable.
   const staleRowsVisible=w.find('table').exists() && w.find('table').text().includes('DEMO-CAPACITY')   // 清掉舊資料＝沒有表格也算通過
   expect(staleRowsVisible && !w.find('.demo').exists()).toBe(false)
 }finally{w.unmount()}
})
