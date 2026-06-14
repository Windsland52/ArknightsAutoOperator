import * as maafwSort from '@nekosu/prettier-plugin-maafw-sort'
import * as multilineArrays from 'prettier-plugin-multiline-arrays'

export default {
    semi: false,
    singleQuote: true,
    trailingComma: 'none',
    tabWidth: 4,
    printWidth: 100,
    multilineArraysWrapThreshold: 0,
    plugins: [
        maafwSort.patchPlugin(multilineArrays)
    ],
    overrides: [
        {
            // MAA pipeline / interface JSON 允许注释（JSONC）；用 jsonc parser 以保留注释。
            files: [
                '*.json'
            ],
            options: {
                parser: 'jsonc',
                tabWidth: 4
            }
        },
        {
            files: [
                '*.jsonc'
            ],
            options: {
                parser: 'jsonc',
                tabWidth: 4
            }
        }
    ]
}
