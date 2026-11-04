import { mount, flushPromises } from '@vue/test-utils'
import { computed, defineComponent, h, onMounted, ref } from 'vue'
import { afterEach, expect, it, vi } from 'vitest'
import Night from '../components/v2/NightAlerts.vue'
afterEach(() => vi.unstubAllGlobals())
// Table.vue:100 renders empty prop for rows.length===0. No external calls.
const Table=defineComponent({props:['rows','empty'],setup(p){return()=>h('table',[h('tr',h('td',p.rows.length?'rows':p.empty))])}})
it.each(['stale','unconfirmed','clock'])('%s空快取不可在表格宣稱目前沒有告警',async(status)=>{
 for(const[k,v]of Object.entries({computed,onMounted,ref}))vi.stubGlobal(k,v)
 vi.stubGlobal('useToast',()=>({showToast:vi.fn()}))
 vi.stubGlobal('useApi',()=>({apiFetch:async()=>({status,status_label:'無法確認現況',message:'DEMO舊快取不可判定現況',rows:[],counts:{total:0,critical:0,warning:0,unmapped:0},data_time:'2026-11-02 01:00:00',age_min:180,fresh_min:30,last_failed:status==='unconfirmed',source:{enabled:true,module_enabled:true,polling_enabled:true,interval_min:5,last_run_at:null,last_run_status:null,last_run_message:null},can_refresh:false,can_edit_settings:false})}))
 const w=mount(Night,{global:{stubs:{V2Table:Table,InfoNote:true}}})
 try{await flushPromises();expect(w.find('[role="status"]').text()).toContain('無法確認現況');expect(w.find('table').text()).not.toContain('目前沒有告警')}finally{w.unmount()}
})
