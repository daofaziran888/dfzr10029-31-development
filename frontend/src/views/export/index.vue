<template>
  <section class="page" data-module="export">
    <header class="page-head">
      <div><h2>数据导出</h2><p class="page-desc">维护导出任务，并按交付范围把数据集快照固化为可复核的导出版本：行数指纹与源快照标识随批次一次锁定，下载入口只返回已固化的导出版本包。</p></div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记导出任务</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
      </div>
    </header>

    <!-- 导出版本固化 -->
    <div class="version-block">
      <div class="page-head">
        <div><h3>导出版本批次</h3><p class="page-desc">读取数据集快照与交付范围后固化批次；批次号、行数指纹、源快照标识为页面一致确认的三项凭据。</p></div>
        <div class="page-actions">
          <button class="btn primary" type="button" data-testid="open-batch-create" @click="openBatchCreate">按交付范围发起批次</button>
          <button class="btn" type="button" data-testid="upload-scope" @click="triggerFilePicker">上传交付范围固化</button>
          <input ref="fileInput" type="file" accept=".csv,text/csv" data-testid="scope-file" style="display:none" @change="handleScopeFile" />
        </div>
      </div>

      <div class="filter-bar">
        <label><span>批次号 / 交付范围 / 源快照标识</span>
          <input v-model="batchKeyword" data-testid="batch-search" placeholder="输入关键字检索批次" @keyup.enter="reloadBatches" />
        </label>
        <button class="btn" type="button" data-testid="batch-query" @click="reloadBatches">查询</button>
        <button class="btn ghost" type="button" data-testid="batch-reset" @click="resetBatchSearch">重置</button>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in batchColumns" :key="column">{{ column }}</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="batch in filteredBatches" :key="String(batch.id)" :data-testid="`batch-row-${batch.id}`" :data-frozen="String(batch.批次状态)">
            <td>{{ batch.批次号 }}</td>
            <td>{{ batch.交付范围 }}</td>
            <td class="fingerprint-cell" :title="batch.源快照标识">{{ batch.源快照标识 }}</td>
            <td class="fingerprint-cell" :title="batch.行数指纹" data-testid="row-fingerprint">{{ batch.行数指纹 }}</td>
            <td>{{ batch.数据行数 }}</td>
            <td>{{ batch.固化时点 }}</td>
            <td>{{ batch.固化时间 }}</td>
            <td><span class="frozen-tag">{{ batch.批次状态 }}</span></td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`manifest-${batch.id}`" @click="openManifest(batch)">导出版本清单</button>
              <button class="link" type="button" :data-testid="`verify-${batch.id}`" @click="verifyBatch(batch)">核对主版本</button>
              <button class="link" type="button" :data-testid="`download-${batch.id}`" @click="downloadPackage(batch)">下载固化包</button>
            </td>
          </tr>
          <tr v-if="!filteredBatches.length">
            <td :colspan="batchColumns.length + 1" class="empty-state">暂无固化批次，可按交付范围发起或上传交付范围文件</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">
        <span>共 {{ filteredBatches.length }} 个导出批次（仅已固化批次可下载）</span>
        <span v-if="batchMessage" class="error-text">{{ batchMessage }}</span>
      </footer>
    </div>

    <!-- 批次明细：清单 + 核对结果 -->
    <div v-if="activeBatch" class="version-block" data-testid="batch-detail">
      <h3>批次 {{ activeBatch.批次号 }} — 导出版本清单与核对</h3>
      <div class="manifest-meta">
        <span>导出批次号：<strong>{{ activeBatch.批次号 }}</strong></span>
        <span>行数指纹：<strong class="fingerprint-cell">{{ activeBatch.行数指纹 }}</strong></span>
        <span>源快照标识：<strong>{{ activeBatch.源快照标识 }}</strong></span>
      </div>
      <table class="data-table" style="margin-top:8px">
        <thead>
          <tr><th v-for="field in manifestFields" :key="field">{{ field }}</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in manifestRows" :key="String(row.id)" :data-testid="`manifest-row-${row.id}`">
            <td v-for="field in manifestFields" :key="field">{{ row[field] ?? '—' }}</td>
          </tr>
          <tr v-if="!manifestRows.length"><td :colspan="manifestFields.length" class="empty-state">暂无清单记录</td></tr>
        </tbody>
      </table>

      <div v-if="verifyResult" class="verify-panel" data-testid="verify-result">
        <strong :class="verifyResult.一致 ? 'ok-text' : 'error-text'">
          主版本核对结果：{{ verifyResult.一致 ? '逐项一致' : '存在差异' }}
        </strong>
        <span>清单行数 {{ verifyResult.清单行数 }} / 主版本行数 {{ verifyResult.主版本行数 }}；核对时间 {{ verifyResult.核对时间 }}</span>
        <ul v-if="!verifyResult.一致">
          <li v-for="item in verifyResult.匹配记录.filter((r: any) => !r.一致)" :key="item.源快照标识">
            {{ item.源快照标识 }} 差异字段：{{ item.差异字段.join('、') || '主版本记录缺失' }}
          </li>
          <li v-if="verifyResult.新增记录.length">主版本新增记录：{{ verifyResult.新增记录.join('、') }}</li>
          <li v-if="verifyResult.缺失记录.length">清单记录在主版本缺失：{{ verifyResult.缺失记录.join('、') }}</li>
        </ul>
        <p v-else class="ok-text">清单每行的源快照标识与主版本逐项核对一致，包内行数可据此复算。</p>
      </div>
    </div>

    <!-- 原有导出任务时间线 -->
    <div class="filter-bar">
      <label><span>开始日期</span><input type="date" v-model="dateFrom" /></label>
      <label><span>结束日期</span><input type="date" v-model="dateTo" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div class="timeline-bar" :class="`status-${item.status}`" :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }" :data-testid="`bar-${item.id}`" :title="`${item.start} ~ ${item.end}`" @click="selectItem(item)">&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <!-- 登记导出任务（原有弹窗） -->
    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate"><h3>登记导出任务</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field"><span>{{ field }}</span><input v-model="createValues[field]" :placeholder="`请输入${field}`" required /></label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>

    <!-- 按交付范围发起批次 -->
    <div v-if="showBatchCreate" class="modal-backdrop" data-testid="batch-modal">
      <form class="modal" @submit.prevent="submitBatchCreate">
        <h3>按交付范围发起导出批次</h3>
        <p class="page-desc">勾选交付范围对应的数据集快照；批次启动即按当前快照固化，拒绝“完成时固化”以避免源表变化导致批次内容漂移。</p>
        <label class="create-field"><span>固化时点（处理边界）</span>
          <select v-model="freezePolicy" data-testid="freeze-policy">
            <option v-for="policy in freezePolicies" :key="policy" :value="policy">{{ policy }}</option>
          </select>
        </label>
        <div class="scope-list">
          <label v-for="snap in snapshotOptions" :key="String(snap.id)" class="scope-option">
            <input type="checkbox" :value="String(snap.快照编号)" v-model="selectedScope" :data-testid="`scope-check-${snap.快照编号}`" />
            <span>{{ snap.快照编号 }} ｜ {{ snap.数据集名称 }} ｜ 快照时间 {{ snap.快照时间 }}</span>
          </label>
          <p v-if="!snapshotOptions.length" class="empty-state">数据集快照模块暂无可用记录</p>
        </div>
        <div class="page-actions" style="margin-top:12px">
          <button class="btn" type="button" @click="showBatchCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-batch" :disabled="submittingBatch">{{ submittingBatch ? '固化中…' : '确认固化' }}</button>
        </div>
      </form>
    </div>
  </section>
</template>
<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { request } from '@/api/client'
const ENDPOINT = '/api/export'
const columns = ["任务编号", "导出对象", "导出格式", "筛选条件", "数据行数", "导出时间", "文件大小", "任务状态"]
const batchColumns = ["批次号", "交付范围", "源快照标识", "行数指纹", "数据行数", "固化时点", "固化时间", "批次状态"]
const manifestFields = ["序号", "源快照标识", "数据集名称", "快照时间", "行数快照", "校验哈希", "快照状态", "来源批次号", "固化时间"]

// 原有时间线
const timelineItems = ref<any[]>([])
const selectedItem = ref<any>(null)
const dateFrom = ref(''); const dateTo = ref('')
const errorMessage = ref(''); const showCreate = ref(false)
const createValues = ref<Record<string,string>>({})
const allItems = ref<any[]>([])
function barLeft(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 0; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return ((new Date(item.start).getTime() - min) / range) * 100 }
function barWidth(item: any) { const all = allItems.value.filter(i => i.start && i.end); if(!all.length) return 20; const min = Math.min(...all.map(i => new Date(i.start).getTime())); const max = Math.max(...all.map(i => new Date(i.end).getTime())); const range = max - min || 86400000; return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100) }
function selectItem(item: any) { selectedItem.value = selectedItem.value?.id === item.id ? null : item }
async function reload() {
  try { const res = await request(ENDPOINT); const data = await res.json(); allItems.value = data.items || []; applyFilter() } catch(e) { errorMessage.value = '加载失败' }
}
function applyFilter() {
  let items = allItems.value
  if(dateFrom.value) items = items.filter((i: any) => i.start >= dateFrom.value)
  if(dateTo.value) items = items.filter((i: any) => i.end <= dateTo.value)
  timelineItems.value = items; selectedItem.value = null
}
function openCreate() { createValues.value = {}; showCreate.value = true }
async function submitCreate() {
  try { const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) }); if(!res.ok) throw new Error('创建失败'); showCreate.value = false; await reload() } catch(e: any) { errorMessage.value = e.message }
}
function exportRows() { window.open(`${ENDPOINT}/export`, '_blank') }

// 导出版本固化
const batches = ref<any[]>([])
const batchKeyword = ref('')
const batchMessage = ref('')
const snapshotOptions = ref<any[]>([])
const freezePolicies = ref<string[]>([])
const freezePolicy = ref('启动时固化')
const selectedScope = ref<string[]>([])
const showBatchCreate = ref(false)
const submittingBatch = ref(false)
const activeBatch = ref<any>(null)
const manifestRows = ref<any[]>([])
const verifyResult = ref<any>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const filteredBatches = computed(() => {
  const key = batchKeyword.value.trim().toLowerCase()
  if (!key) return batches.value
  return batches.value.filter((b: any) =>
    String(b.批次号).toLowerCase().includes(key)
    || String(b.交付范围).toLowerCase().includes(key)
    || String(b.源快照标识).toLowerCase().includes(key))
})

async function reloadBatches() {
  batchMessage.value = ''
  try {
    const query = batchKeyword.value.trim() ? `?keyword=${encodeURIComponent(batchKeyword.value.trim())}` : ''
    const res = await request(`${ENDPOINT}/batches${query}`)
    if (!res.ok) throw new Error('批次列表读取失败')
    const data = await res.json()
    batches.value = data.items || []
  } catch (e: any) {
    batchMessage.value = e.message
  }
}

function resetBatchSearch() { batchKeyword.value = ''; void reloadBatches() }

async function loadSnapshotOptions() {
  const res = await request(`${ENDPOINT}/snapshots/options`)
  if (!res.ok) return
  const data = await res.json()
  snapshotOptions.value = data.items || []
  freezePolicies.value = data.freeze_policies || ['启动时固化']
  freezePolicy.value = freezePolicies.value[0] || '启动时固化'
}

function openBatchCreate() {
  selectedScope.value = []
  batchMessage.value = ''
  showBatchCreate.value = true
}

async function submitBatchCreate() {
  if (!selectedScope.value.length) { batchMessage.value = '请至少勾选一个交付范围快照'; return }
  submittingBatch.value = true
  batchMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/batches`, {
      method: 'POST',
      body: JSON.stringify({ scope: selectedScope.value, freeze_policy: freezePolicy.value }),
    })
    const payload = await res.json()
    if (!res.ok || !payload.ok) throw new Error(payload.message || '批次固化失败')
    showBatchCreate.value = false
    await reloadBatches()
    await openManifest(payload.entry)
  } catch (e: any) {
    batchMessage.value = e.message
  } finally {
    submittingBatch.value = false
  }
}

function triggerFilePicker() { fileInput.value?.click() }

async function handleScopeFile(event: Event) {
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) return
  batchMessage.value = ''
  try {
    const content = await file.text()
    const res = await request(
      `${ENDPOINT}/batches/upload?filename=${encodeURIComponent(file.name)}&freeze_policy=${encodeURIComponent(freezePolicy.value)}`,
      { method: 'POST', body: content, headers: { 'Content-Type': 'text/csv; charset=utf-8' } },
    )
    const payload = await res.json()
    if (!res.ok || !payload.ok) throw new Error(payload.message || '上传固化失败')
    await reloadBatches()
    await openManifest(payload.entry)
  } catch (e: any) {
    batchMessage.value = e.message
  } finally {
    input.value = ''
  }
}

async function openManifest(batch: any) {
  activeBatch.value = batch
  verifyResult.value = null
  try {
    const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(batch.批次号)}/manifests`)
    if (!res.ok) throw new Error('导出版本清单读取失败')
    manifestRows.value = (await res.json()).items || []
  } catch (e: any) {
    batchMessage.value = e.message
    manifestRows.value = []
  }
}

async function verifyBatch(batch: any) {
  await openManifest(batch)
  try {
    const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(batch.批次号)}/verify`)
    if (!res.ok) throw new Error('主版本核对失败')
    verifyResult.value = await res.json()
  } catch (e: any) {
    batchMessage.value = e.message
  }
}

async function downloadPackage(batch: any) {
  if (batch.批次状态 !== '已固化') { batchMessage.value = '下载入口只返回已经固化的导出版本包'; return }
  batchMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/batches/${encodeURIComponent(batch.批次号)}/download`)
    if (!res.ok) throw new Error('固化包下载失败')
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = `${batch.批次号}.zip`
    link.click()
    URL.revokeObjectURL(url)
  } catch (e: any) {
    batchMessage.value = e.message
  }
}

onMounted(() => {
  void reload()
  void reloadBatches()
  void loadSnapshotOptions()
})
</script>
<style scoped>
.version-block { background: #fff; border: 1px solid var(--border); border-radius: 10px; padding: 14px 16px; margin-bottom: 16px; }
.version-block h3 { margin: 0 0 8px; font-size: 15px; }
.fingerprint-cell { font-family: monospace; font-size: 12px; max-width: 220px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.frozen-tag { background: #e7f6ee; color: #15803d; border-radius: 999px; padding: 2px 10px; font-size: 12px; }
.manifest-meta { display: flex; flex-wrap: wrap; gap: 18px; font-size: 13px; color: var(--muted); background: #f8fafc; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
.verify-panel { margin-top: 10px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; flex-direction: column; gap: 6px; font-size: 13px; background: #f8fafc; }
.ok-text { color: #15803d; }
.scope-list { display: flex; flex-direction: column; gap: 8px; max-height: 260px; overflow-y: auto; border: 1px solid var(--border); border-radius: 8px; padding: 10px; }
.scope-option { display: flex; gap: 8px; align-items: center; font-size: 13px; }
</style>
