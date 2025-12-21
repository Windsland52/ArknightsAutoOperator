<template>
  <div class="container">
    <header>
      <h1>Arknights Auto Operator</h1>
      <p class="subtitle">明日方舟自动作战工具</p>
    </header>

    <main>
      <section class="config-section">
        <h2>Win32 控制器配置</h2>

        <!-- 窗口扫描 -->
        <div class="form-group">
          <label>窗口选择</label>
          <div class="button-row">
            <button @click="scanWindows" class="btn btn-scan" :disabled="scanning">
              {{ scanning ? '扫描中...' : '扫描窗口' }}
            </button>
            <button v-if="windows.length > 0 && !showWindowList" @click="showWindowList = true" class="btn btn-secondary">
              重新选择
            </button>
          </div>
          <div v-if="windows.length > 0 && showWindowList" class="window-list">
            <div
              v-for="(win, index) in windows"
              :key="index"
              @click="selectWindow(win)"
              :class="['window-item', { selected: selectedWindow === win }]"
            >
              <div class="window-title">{{ win.title }}</div>
              <div class="window-class">类名: {{ win.class_name }}</div>
            </div>
          </div>
          <div v-if="selectedWindow && !showWindowList" class="selected-window-info">
            <strong>已选择:</strong> {{ selectedWindow.title }}
            <br>
            <small>类名: {{ selectedWindow.class_name }}</small>
          </div>
        </div>

        <!-- 手动输入（可选） -->
        <div class="form-group">
          <label for="classRegex">窗口类名正则表达式（可选）</label>
          <input
            type="text"
            id="classRegex"
            v-model="config.class_regex"
            placeholder="例如: UnityWndClass"
          >
          <span class="help-text">留空则使用扫描选择的窗口</span>
        </div>

        <div class="form-group">
          <label for="windowRegex">窗口标题正则表达式（可选）</label>
          <input
            type="text"
            id="windowRegex"
            v-model="config.window_regex"
            placeholder="例如: MuMu模拟器"
          >
          <span class="help-text">留空则使用扫描选择的窗口</span>
        </div>

        <div class="form-group">
          <label for="screencap">截图方法</label>
          <select id="screencap" v-model="config.screencap">
            <option value="GDI">GDI - 快速，中等兼容性</option>
            <option value="FramePool">FramePool - 极快，支持后台 (推荐，Win10 1903+)</option>
            <option value="DXGI_DesktopDup">DXGI DesktopDup - 极快，低兼容性</option>
            <option value="DXGI_DesktopDup_Window">DXGI DesktopDup Window - 极快，低兼容性</option>
            <option value="PrintWindow">PrintWindow - 中速，支持后台</option>
            <option value="ScreenDC">ScreenDC - 快速，高兼容性</option>
          </select>
          <span class="help-text">FramePool推荐，速度快且支持后台运行</span>
        </div>

        <div class="form-group">
          <label for="mouse">鼠标输入方法</label>
          <select id="mouse" v-model="config.mouse">
            <option value="Seize">Seize - 高兼容性，抢占鼠标</option>
            <option value="SendMessage">SendMessage - 中等兼容性，支持后台</option>
            <option value="PostMessage">PostMessage - 中等兼容性，支持后台 (推荐)</option>
            <option value="LegacyEvent">LegacyEvent - 低兼容性，抢占鼠标</option>
            <option value="PostThreadMessage">PostThreadMessage - 低兼容性，支持后台</option>
            <option value="SendMessageWithCursorPos">SendMessageWithCursorPos - 短暂抢占，支持后台</option>
            <option value="PostMessageWithCursorPos">PostMessageWithCursorPos - 短暂抢占，支持后台</option>
          </select>
          <span class="help-text">PostMessage推荐，异步且支持后台</span>
        </div>

        <div class="form-group">
          <label for="keyboard">键盘输入方法</label>
          <select id="keyboard" v-model="config.keyboard">
            <option value="Seize">Seize - 高兼容性，抢占键盘</option>
            <option value="SendMessage">SendMessage - 中等兼容性，支持后台</option>
            <option value="PostMessage">PostMessage - 中等兼容性，支持后台 (推荐)</option>
            <option value="LegacyEvent">LegacyEvent - 低兼容性，抢占键盘</option>
            <option value="PostThreadMessage">PostThreadMessage - 低兼容性，支持后台</option>
            <option value="SendMessageWithCursorPos">SendMessageWithCursorPos - 短暂抢占，支持后台</option>
            <option value="PostMessageWithCursorPos">PostMessageWithCursorPos - 短暂抢占，支持后台</option>
          </select>
          <span class="help-text">PostMessage推荐，异步且支持后台</span>
        </div>

        <div class="button-group">
          <button @click="testConnection" class="btn btn-primary" :disabled="testing">
            {{ testing ? '测试中...' : '测试连接' }}
          </button>
          <button @click="saveConfig" class="btn btn-secondary" :disabled="saving">
            {{ saving ? '保存中...' : '保存配置' }}
          </button>
        </div>

        <div v-if="statusMessage" :class="['status-message', 'show', statusType]">
          {{ statusMessage }}
        </div>
      </section>
    </main>

    <footer>
      <p>Powered by MaaFramework & Wails</p>
    </footer>
  </div>
</template>

<script setup>
import { ref, onMounted } from 'vue'
import { TestWin32Connection, SaveWin32Config, LoadWin32Config, ScanAllWindows } from '../wailsjs/go/main/App'

const config = ref({
  class_regex: '',
  window_regex: 'MuMu',
  screencap: 'FramePool',
  mouse: 'PostMessage',
  keyboard: 'PostMessage'
})

const windows = ref([])
const selectedWindow = ref(null)
const showWindowList = ref(true)
const scanning = ref(false)
const testing = ref(false)
const saving = ref(false)
const statusMessage = ref('')
const statusType = ref('info')

// 扫描所有窗口
async function scanWindows() {
  scanning.value = true
  showWindowList.value = true
  statusMessage.value = '正在扫描窗口...'
  statusType.value = 'info'

  try {
    const result = await ScanAllWindows()
    let allWindows = result || []

    // 如果填写了类名或标题正则，进行过滤
    if (config.value.class_regex || config.value.window_regex) {
      const classRegex = config.value.class_regex ? new RegExp(config.value.class_regex) : null
      const titleRegex = config.value.window_regex ? new RegExp(config.value.window_regex) : null

      allWindows = allWindows.filter(win => {
        const classMatch = !classRegex || classRegex.test(win.class_name)
        const titleMatch = !titleRegex || titleRegex.test(win.title)
        return classMatch && titleMatch
      })

      if (allWindows.length === 0) {
        statusMessage.value = '未找到匹配的窗口，请检查正则表达式'
        statusType.value = 'error'
        windows.value = []
        return
      }

      statusMessage.value = `找到 ${allWindows.length} 个匹配的窗口`
      statusType.value = 'success'
    } else {
      if (allWindows.length === 0) {
        statusMessage.value = '未找到任何窗口'
        statusType.value = 'error'
      } else {
        statusMessage.value = `找到 ${allWindows.length} 个窗口`
        statusType.value = 'success'
      }
    }

    windows.value = allWindows
  } catch (error) {
    statusMessage.value = `扫描失败: ${error}`
    statusType.value = 'error'
  } finally {
    scanning.value = false
  }
}

// 选择窗口
function selectWindow(win) {
  selectedWindow.value = win
  // 自动填充类名和标题（作为精确匹配）
  config.value.class_regex = win.class_name
  config.value.window_regex = win.title
  // 隐藏窗口列表
  showWindowList.value = false
  statusMessage.value = `已选择窗口: ${win.title}`
  statusType.value = 'success'
}

// 测试连接
async function testConnection() {
  if (!config.value.class_regex && !config.value.window_regex) {
    statusMessage.value = '请先扫描并选择窗口，或手动填写窗口信息'
    statusType.value = 'error'
    return
  }

  testing.value = true
  statusMessage.value = '正在测试连接...'
  statusType.value = 'info'

  try {
    const result = await TestWin32Connection(config.value)

    if (result.success) {
      statusMessage.value = `✓ 连接成功！\n找到窗口: ${result.window_title}`
      statusType.value = 'success'
    } else {
      statusMessage.value = `✗ 连接失败: ${result.error}`
      statusType.value = 'error'
    }
  } catch (error) {
    statusMessage.value = `✗ 测试失败: ${error}`
    statusType.value = 'error'
  } finally {
    testing.value = false
  }
}

// 保存配置
async function saveConfig() {
  if (!config.value.class_regex && !config.value.window_regex) {
    statusMessage.value = '请先扫描并选择窗口，或手动填写窗口信息'
    statusType.value = 'error'
    return
  }

  saving.value = true
  statusMessage.value = '正在保存配置...'
  statusType.value = 'info'

  try {
    await SaveWin32Config(config.value)
    statusMessage.value = '✓ 配置已保存'
    statusType.value = 'success'
  } catch (error) {
    statusMessage.value = `✗ 保存失败: ${error}`
    statusType.value = 'error'
  } finally {
    saving.value = false
  }
}

// 加载配置
async function loadConfig() {
  try {
    const savedConfig = await LoadWin32Config()
    if (savedConfig) {
      config.value = savedConfig
    }
  } catch (error) {
    console.log('No saved configuration found, using defaults')
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<style scoped>
* {
  margin: 0;
  padding: 0;
  box-sizing: border-box;
}

.container {
  max-width: 900px;
  margin: 0 auto;
  background: white;
  min-height: 100vh;
}

header {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
  padding: 30px;
  text-align: center;
}

header h1 {
  font-size: 2em;
  margin-bottom: 10px;
}

.subtitle {
  font-size: 1em;
  opacity: 0.9;
}

main {
  padding: 30px;
}

.config-section {
  margin-bottom: 30px;
}

.config-section h2 {
  color: #333;
  margin-bottom: 20px;
  padding-bottom: 10px;
  border-bottom: 2px solid #667eea;
}

.form-group {
  margin-bottom: 20px;
}

.form-group label {
  display: block;
  font-weight: 600;
  color: #333;
  margin-bottom: 8px;
}

.form-group input[type="text"],
.form-group select {
  width: 100%;
  padding: 10px 15px;
  border: 2px solid #e0e0e0;
  border-radius: 6px;
  font-size: 14px;
  transition: border-color 0.3s;
}

.form-group input[type="text"]:focus,
.form-group select:focus {
  outline: none;
  border-color: #667eea;
}

.help-text {
  display: block;
  font-size: 12px;
  color: #666;
  margin-top: 5px;
}

.button-row {
  margin-bottom: 10px;
}

.btn {
  padding: 10px 20px;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.3s;
}

.btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}

.btn-scan {
  background: #4CAF50;
  color: white;
}

.btn-scan:hover:not(:disabled) {
  background: #45a049;
}

.window-list {
  max-height: 300px;
  overflow-y: auto;
  border: 2px solid #e0e0e0;
  border-radius: 6px;
  margin-top: 10px;
}

.window-item {
  padding: 12px;
  border-bottom: 1px solid #e0e0e0;
  cursor: pointer;
  transition: background 0.2s;
}

.window-item:hover {
  background: #f5f5f5;
}

.window-item.selected {
  background: #e3f2fd;
  border-left: 4px solid #667eea;
}

.window-item:last-child {
  border-bottom: none;
}

.window-title {
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}

.window-class {
  font-size: 12px;
  color: #666;
}

.selected-window-info {
  padding: 12px;
  background: #e3f2fd;
  border: 2px solid #2196F3;
  border-radius: 6px;
  margin-top: 10px;
}

.selected-window-info strong {
  color: #1976D2;
}

.selected-window-info small {
  color: #666;
}

.button-group {
  display: flex;
  gap: 10px;
  margin-top: 25px;
}

.button-group .btn {
  flex: 1;
  padding: 12px 24px;
  font-size: 16px;
}

.btn-primary {
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
  color: white;
}

.btn-primary:hover:not(:disabled) {
  transform: translateY(-2px);
  box-shadow: 0 5px 15px rgba(102, 126, 234, 0.4);
}

.btn-secondary {
  background: #f0f0f0;
  color: #333;
}

.btn-secondary:hover:not(:disabled) {
  background: #e0e0e0;
}

.status-message {
  margin-top: 20px;
  padding: 15px;
  border-radius: 6px;
  font-size: 14px;
  display: none;
  white-space: pre-line;
}

.status-message.show {
  display: block;
}

.status-message.success {
  background: #d4edda;
  color: #155724;
  border: 1px solid #c3e6cb;
}

.status-message.error {
  background: #f8d7da;
  color: #721c24;
  border: 1px solid #f5c6cb;
}

.status-message.info {
  background: #d1ecf1;
  color: #0c5460;
  border: 1px solid #bee5eb;
}

.info-section {
  background: #f8f9fa;
  padding: 20px;
  border-radius: 8px;
  margin-top: 30px;
}

.info-section h3 {
  color: #333;
  margin-bottom: 15px;
}

.info-section ul {
  list-style-position: inside;
  color: #555;
}

.info-section li {
  margin-bottom: 10px;
  line-height: 1.6;
}

.info-section ul ul {
  margin-left: 20px;
  margin-top: 5px;
}

footer {
  background: #f8f9fa;
  padding: 20px;
  text-align: center;
  color: #666;
  font-size: 14px;
}
</style>
