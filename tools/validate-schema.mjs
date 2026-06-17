// 精简版项目结构校验（不依赖 create-maa-project 的 maa-project.json/lock/maatools.config）。
// 校验：interface.json 形状 + controller/resource/import 路径存在 + MaaFW JSON 用正斜杠。
// 用法：pnpm check:schema
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'

const interfaceJson = readJson('interface.json')

assertEqual(interfaceJson.interface_version, 2, 'interface.json interface_version must be 2')
assertSlug(interfaceJson.name, 'interface.json name')
assertNonEmptyString(interfaceJson.label, 'interface.json label')
assertVersion(interfaceJson.version, 'interface.json version', true)
assertArrayOfRecords(interfaceJson.controller, 'interface.json controller')
assertArrayOfRecords(interfaceJson.resource, 'interface.json resource')
// import 可省略（MFAAvalonia 风格下用于引入额外 pipeline，本项目无外部依赖时省略）。
assertArrayOfStrings(interfaceJson.import ?? [], 'interface.json import')

const CONTROLLER_TYPES = [
    'Adb',
    'Win32',
    'MacOS',
    'PlayCover',
    'Gamepad',
    'WlRoots'
]
for (const [
    i,
    c
] of interfaceJson.controller.entries()) {
    assertNonEmptyString(c.name, `interface.json controller[${i}].name`)
    assertEnum(c.type, CONTROLLER_TYPES, `interface.json controller[${i}].type`)
}

for (const [
    i,
    r
] of interfaceJson.resource.entries()) {
    assertNonEmptyString(r.name, `interface.json resource[${i}].name`)
    assertArrayOfStrings(r.path, `interface.json resource[${i}].path`)
    for (const p of r.path) {
        assertForwardRelativePath(p, `interface.json resource[${i}].path entry`)
    }
}

// 第一个资源包路径必须是 ./resource/base（约定）。
if (interfaceJson.resource[0]?.path?.[0] !== './resource/base') {
    throw new Error('interface.json resource[0].path[0] must be ./resource/base')
}

// 所有 import / resource 路径必须实际存在。
for (const p of [
    ...(interfaceJson.import ?? []),
    ...interfaceJson.resource.flatMap((r) => r.path)
]) {
    if (!existsSync(p)) throw new Error(`referenced path does not exist: ${p}`)
}

// icon（可选）若声明必须存在。
if (interfaceJson.icon && !existsSync(interfaceJson.icon)) {
    throw new Error(`interface.json icon not found: ${interfaceJson.icon}`)
}

// MaaFW JSON 路径必须用正斜杠（仅文本检查；文件可为 JSONC，不解析）。
for (const path of walkJsonFiles([
    'interface.json',
    'tasks',
    'resource'
])) {
    const content = readFileSync(path, 'utf8')
    if (content.includes('\\')) {
        throw new Error(`MaaFW JSON paths must use forward slashes: ${path}`)
    }
}

console.log('[OK] project schema shape is valid')

// --- helpers ---

// MaaFW 项目约定 JSON 可带 // 行注释与 /* */ 块注释（JSONC）。
// JSON.parse 不认注释，故解析前剥离（跳过字符串字面量内的注释符号）。
function stripJsonc(text) {
    let out = ''
    let i = 0
    const n = text.length
    while (i < n) {
        const ch = text[i]
        const next = text[i + 1]
        // 字符串字面量：原样复制到闭合引号（处理 \" 转义）
        if (ch === '"') {
            out += ch
            i += 1
            while (i < n) {
                const c = text[i]
                out += c
                i += 1
                if (c === '\\' && i < n) {
                    out += text[i]
                    i += 1
                } else if (c === '"') {
                    break
                }
            }
            continue
        }
        // 行注释
        if (ch === '/' && next === '/') {
            while (i < n && text[i] !== '\n') i += 1
            continue
        }
        // 块注释
        if (ch === '/' && next === '*') {
            i += 2
            while (i < n && !(text[i] === '*' && text[i + 1] === '/')) i += 1
            i += 2
            out += ' '
            continue
        }
        out += ch
        i += 1
    }
    return out
}

function readJson(path) {
    if (!existsSync(path)) throw new Error(`${path} is missing`)
    return JSON.parse(stripJsonc(readFileSync(path, 'utf8')))
}

function assertEqual(actual, expected, message) {
    if (actual !== expected) throw new Error(message)
}

function assertNonEmptyString(value, label) {
    if (typeof value !== 'string' || value.trim() === '') {
        throw new Error(`${label} must be a non-empty string`)
    }
}

function assertSlug(value, label) {
    assertNonEmptyString(value, label)
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(value)) {
        throw new Error(`${label} must be an ASCII kebab-case slug`)
    }
}

function assertVersion(value, label, withV) {
    assertNonEmptyString(value, label)
    const pattern = withV
        ? /^v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/
        : /^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/
    if (!pattern.test(value)) {
        throw new Error(`${label} must be a SemVer version${withV ? ' with v prefix' : ''}`)
    }
}

function assertEnum(value, allowed, label) {
    if (!allowed.includes(value)) {
        throw new Error(`${label} must be one of: ${allowed.join(', ')}`)
    }
}

function assertArrayOfRecords(value, label) {
    if (
        !Array.isArray(value) ||
        value.some((item) => typeof item !== 'object' || item === null || Array.isArray(item))
    ) {
        throw new Error(`${label} must be an array of objects`)
    }
}

function assertArrayOfStrings(value, label) {
    if (!Array.isArray(value) || value.some((item) => typeof item !== 'string')) {
        throw new Error(`${label} must be an array of strings`)
    }
}

function assertForwardRelativePath(value, label) {
    assertNonEmptyString(value, label)
    if (value.startsWith('/') || value.includes('..') || value.includes('\\')) {
        throw new Error(`${label} must be a forward-slash relative path`)
    }
}

function walkJsonFiles(paths) {
    const files = []
    for (const path of paths) {
        if (!existsSync(path)) continue
        const stat = statSync(path)
        if (stat.isDirectory()) {
            for (const entry of readdirSync(path)) {
                files.push(
                    ...walkJsonFiles([
                        `${path}/${entry}`
                    ])
                )
            }
        } else if (path.endsWith('.json')) {
            files.push(path)
        }
    }
    return files
}
