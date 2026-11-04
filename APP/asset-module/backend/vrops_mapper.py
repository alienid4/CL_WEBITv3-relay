"""把 Aria Operations 的回應轉成「跟 RVTools 匯入器吃的一模一樣」的記錄。

## 目標：下游完全不用改

RVTools 路徑（rvtools_import.parse_rvtools / parse_extra_sheets）產出兩樣東西：
  1. records：每台 VM 一筆 dict（vm_uuid／hostname／ip／os／is_vm／vm_name／dns_name／esxi_host／powerstate／
     moref／cluster／datastore／vi_sdk_server／raw），寫成 source_record(source='vcenter') 並建資產；
  2. extra_sheets：{分頁名: [dict…]}，寫成 source_record(source='vcenter_extra:<分頁>')，
     主機清單、儲存容量、規格比對、影響範圍（runs_on／member_of）都從這裡讀。
這支產出同樣形狀，欄名沿用 RVTools 的表頭（Host、Cluster、ESX Version、Capacity MiB…），
再交給同一個寫入器（rvtools_import.import_records），所以去重與 machine_key 規則完全不變。

## 刻意的取捨

* vm_uuid 只用 BIOS UUID（RVTools 的 VM UUID 就是這個）。Aria 的 instance UUID 是另一個值，
  拿來頂替會讓同一台 VM 跟之前 RVTools 匯入的資產對不上、被當成新機器。取不到就留空，
  交給身分解析退到主機名／IP 比對（較弱但不會錯併）。
* 查不到的欄位留 None，不猜。
* 輸出順序固定（依名稱排序），重複匯入同一份快照結果一模一樣（寫入器的 source_key 含序號，順序穩定才會冪等）。

⚠️ 屬性鍵與回應格式尚未在實機驗證，見 vrops_constants.py。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import vrops_constants as C

SOURCE_LABEL = "Aria Operations"

_POWER = {"poweredon": "poweredOn", "poweredoff": "poweredOff", "suspended": "suspended"}


@dataclass
class Snapshot:
    vms: list[dict] = field(default_factory=list)
    hosts: list[dict] = field(default_factory=list)
    clusters: list[dict] = field(default_factory=list)
    datastores: list[dict] = field(default_factory=list)
    props: dict[str, dict[str, str]] = field(default_factory=dict)
    cluster_hosts: dict[str, list[str]] = field(default_factory=dict)     # 叢集 id → 主機 id
    host_vms: dict[str, list[str]] = field(default_factory=dict)          # 主機 id → VM id
    ds_vms: dict[str, list[str]] = field(default_factory=dict)            # datastore id → VM id
    ds_hosts: dict[str, list[str]] = field(default_factory=dict)          # datastore id → 主機 id
    vcenter_labels: dict[str, str] = field(default_factory=dict)          # vCenter 實例 id → 顯示名稱（設定檔可補）


def rid(res: dict) -> str:
    return str(res.get("identifier") or "")


def rname(res: dict) -> str:
    return str(((res.get("resourceKey") or {}).get("name")) or "").strip()


def ident(res: dict, name: str) -> str | None:
    for it in ((res.get("resourceKey") or {}).get("resourceIdentifiers")) or []:
        if ((it.get("identifierType") or {}).get("name")) == name and it.get("value") not in (None, ""):
            return str(it["value"]).strip()
    return None


def first(props: dict[str, str], keys: tuple[str, ...]) -> str | None:
    for k in keys:
        v = props.get(k)
        if v not in (None, ""):
            return str(v).strip()
    return None


def _num(v: str | None) -> float | None:
    try:
        return float(v) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _mib_from_gb(v: str | None) -> int | None:
    n = _num(v)
    return int(round(n * C.GB_TO_MIB)) if n is not None else None


def _power(v: str | None) -> str | None:
    if not v:
        return None
    key = v.replace(" ", "").lower()
    return _POWER.get(key, v)


def _name_of(snapshot_ids: dict[str, dict], rid_: str | None) -> str | None:
    r = snapshot_ids.get(rid_ or "")
    return rname(r) if r else None


def map_snapshot(s: Snapshot) -> dict:
    """回 {"records": [...], "extra_sheets": {...}, "stats": {...}}。"""
    by_id: dict[str, dict] = {rid(r): r for group in (s.vms, s.hosts, s.clusters, s.datastores) for r in group}

    # 反向索引：VM → 主機／叢集／datastore（關聯優先，沒有再退回屬性）
    vm_host: dict[str, str] = {}
    for hid, vids in s.host_vms.items():
        for v in vids:
            vm_host.setdefault(v, hid)
    host_cluster: dict[str, str] = {}
    for cid, hids in s.cluster_hosts.items():
        for h in hids:
            host_cluster.setdefault(h, cid)
    vm_ds: dict[str, list[str]] = {}
    for did, vids in s.ds_vms.items():
        for v in vids:
            vm_ds.setdefault(v, []).append(did)

    records: list[dict] = []
    vcpu: list[dict] = []
    vmem: list[dict] = []
    vdisk: list[dict] = []
    for vm in sorted(s.vms, key=lambda r: (rname(r).lower(), rid(r))):
        name = rname(vm)
        if not name:
            continue
        p = s.props.get(rid(vm), {})
        moref = ident(vm, "VMEntityObjectID")
        host_name = _name_of(by_id, vm_host.get(rid(vm))) or first(p, C.P_PARENT_HOST)
        cl_id = host_cluster.get(vm_host.get(rid(vm), ""))
        cluster = _name_of(by_id, cl_id) or first(p, C.P_PARENT_CLUSTER)
        ds_names = sorted({n for n in (_name_of(by_id, d) for d in vm_ds.get(rid(vm), [])) if n})
        datastore = ds_names[0] if ds_names else first(p, C.P_PARENT_DATASTORE)
        guest_host = first(p, C.P_GUEST_HOSTNAME)
        vc_id = ident(vm, "VMEntityVCID")
        vi_server = s.vcenter_labels.get(vc_id or "")
        uuid = first(p, C.P_BIOS_UUID)
        ip = first(p, C.P_GUEST_IP)
        os_name = first(p, C.P_GUEST_OS)
        power = _power(first(p, C.P_POWER))
        # raw：沿用 RVTools vInfo 的原文表頭，讓 payload 長得一樣；另附 Aria 才有的識別碼
        raw = {k: v for k, v in {
            "VM": name, "DNS Name": guest_host, "Powerstate": power, "Primary IP Address": ip,
            "OS according to the VMware Tools": os_name, "Host": host_name, "VM UUID": uuid, "VM ID": moref,
            "Cluster": cluster, "VI SDK Server": vi_server,
            "Path": f"[{datastore}] {name}/{name}.vmx" if datastore else None,
            "Aria Resource ID": rid(vm), "Aria VM Instance UUID": ident(vm, "VMEntityInstanceUUID"),
            "Aria Datastores": ", ".join(ds_names) if len(ds_names) > 1 else None,
        }.items() if v not in (None, "")}
        records.append({
            "vm_uuid": uuid, "hostname": guest_host or name, "ip": ip, "os": os_name, "is_vm": 1,
            "vm_name": name, "dns_name": guest_host, "esxi_host": host_name, "powerstate": power,
            "moref": moref, "cluster": cluster, "datastore": datastore, "vi_sdk_server": vi_server, "raw": raw,
        })
        # 規格（measured_hw 讀 vCPU／vMemory／vDisk 分頁的 VM UUID／VM、CPUs、Size MiB、Capacity MiB）
        cpus = _num(first(p, C.P_CPU_COUNT))
        if cpus is not None:
            vcpu.append({"VM": name, "VM UUID": uuid, "CPUs": int(cpus)})
        mem_kb, mem_mb = _num(first(p, C.P_MEM_KB)), _num(first(p, C.P_MEM_MB))
        mem = int(round(mem_kb * C.KB_TO_MIB)) if mem_kb is not None else (int(mem_mb) if mem_mb is not None else None)
        if mem is not None:
            vmem.append({"VM": name, "VM UUID": uuid, "Size MiB": mem})
        disk = _mib_from_gb(first(p, C.P_DISK_GB))
        if disk is not None:
            vdisk.append({"VM": name, "VM UUID": uuid, "Disk": "Aria 合計", "Disk UUID": f"aria-{rid(vm)}", "Capacity MiB": disk})

    vhost: list[dict] = []
    for h in sorted(s.hosts, key=lambda r: (rname(r).lower(), rid(r))):
        hp = s.props.get(rid(h), {})
        cl = _name_of(by_id, host_cluster.get(rid(h))) or first(hp, C.P_PARENT_CLUSTER)
        ver, build = first(hp, C.P_ESX_VERSION), first(hp, C.P_ESX_BUILD)
        esx = f"VMware ESXi {ver} build-{build}" if ver and build else (f"VMware ESXi {ver}" if ver else None)
        vc_label = s.vcenter_labels.get(ident(h, "VMEntityVCID") or "")
        row = {"Host": rname(h), "Cluster": cl, "Datacenter": first(hp, C.P_PARENT_DATACENTER),
               "ESX Version": esx, "VI SDK Server": vc_label, "Object ID": ident(h, "VMEntityObjectID")}
        vhost.append({k: v for k, v in row.items() if v not in (None, "")})

    vcluster = [{k: v for k, v in {"Name": rname(c), "Object ID": ident(c, "VMEntityObjectID"),
                                   "VI SDK Server": s.vcenter_labels.get(ident(c, "VMEntityVCID") or "")}.items() if v not in (None, "")}
                for c in sorted(s.clusters, key=lambda r: (rname(r).lower(), rid(r)))]

    vds: list[dict] = []
    for d in sorted(s.datastores, key=lambda r: (rname(r).lower(), rid(r))):
        dp = s.props.get(rid(d), {})
        cap = _mib_from_gb(first(dp, C.P_DS_CAPACITY_GB))
        free = _mib_from_gb(first(dp, C.P_DS_FREE_GB))
        hosts = sorted({n for n in (_name_of(by_id, x) for x in s.ds_hosts.get(rid(d), [])) if n})
        row = {"Name": rname(d), "Type": first(dp, C.P_DS_TYPE), "URL": first(dp, C.P_DS_URL),
               "Capacity MiB": cap, "Free MiB": free,
               "In Use MiB": (cap - free) if cap is not None and free is not None else None,
               "Hosts": ", ".join(hosts) if hosts else None, "Object ID": ident(d, "VMEntityObjectID"),
               "VI SDK Server": s.vcenter_labels.get(ident(d, "VMEntityVCID") or "")}
        vds.append({k: v for k, v in row.items() if v not in (None, "")})

    sheets = {"vHost": vhost, "vCluster": vcluster, "vDatastore": vds, "vCPU": vcpu, "vMemory": vmem, "vDisk": vdisk}
    return {
        "records": records,
        "extra_sheets": {k: v for k, v in sheets.items() if v},
        "stats": {"vms": len(records), "hosts": len(vhost), "clusters": len(vcluster), "datastores": len(vds)},
    }
