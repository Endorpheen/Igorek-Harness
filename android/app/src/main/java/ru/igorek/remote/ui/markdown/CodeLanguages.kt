package ru.igorek.remote.ui.markdown

// Language syntax highlighting profiles data: comment markers, quotes, and sets
// of keywords/types. Only tables are present; the scanner operates in CodeHighlight.kt.
internal data class CodeLanguage(
    val lineComments: List<String> = emptyList(),
    val blockComments: List<Pair<String, String>> = emptyList(),
    val quotes: List<String> = listOf("\"", "'"),
    val keywords: Set<String> = emptySet(),
    val types: Set<String> = emptySet(),
    val caseInsensitive: Boolean = false,
    val tagMarkup: Boolean = false,
    val colonKeys: Boolean = false,
)

private val CTypes = setOf(
    "int8_t", "int16_t", "int32_t", "int64_t", "size_t", "string", "string_view",
    "uint8_t", "uint16_t", "uint32_t", "uint64_t", "unique_ptr", "shared_ptr",
    "vector", "map", "set", "unordered_map", "unordered_set", "optional", "pair",
)

internal val CODE_LANGUAGES: Map<String, CodeLanguage> = mapOf(
    "python" to CodeLanguage(
        lineComments = listOf("#"),
        quotes = listOf("\"\"\"", "'''", "\"", "'"),
        keywords = setOf(
            "and", "as", "assert", "async", "await", "break", "class", "continue", "def", "del",
            "elif", "else", "except", "False", "finally", "for", "from", "global", "if", "import",
            "in", "is", "lambda", "match", "case", "None", "nonlocal", "not", "or", "pass",
            "raise", "return", "self", "True", "try", "while", "with", "yield",
        ),
        types = setOf(
            "Any", "Callable", "Dict", "Exception", "Iterable", "Iterator", "List", "Mapping",
            "Optional", "Sequence", "Set", "Tuple", "TypeError", "Union", "ValueError",
            "bool", "bytes", "dict", "float", "int", "list", "object", "set", "str", "tuple",
        ),
    ),
    "kotlin" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        quotes = listOf("\"\"\"", "\"", "'"),
        keywords = setOf(
            "abstract", "actual", "annotation", "as", "break", "by", "catch", "class", "companion",
            "const", "constructor", "continue", "crossinline", "data", "do", "else", "enum",
            "expect", "external", "false", "final", "finally", "for", "fun", "get", "if", "import",
            "in", "infix", "init", "inline", "inner", "interface", "internal", "is", "lateinit",
            "noinline", "null", "object", "open", "operator", "out", "override", "package",
            "private", "protected", "public", "reified", "return", "sealed", "set", "super",
            "suspend", "tailrec", "this", "throw", "true", "try", "typealias", "val", "var",
            "vararg", "when", "where", "while",
        ),
        types = setOf(
            "Any", "Array", "Boolean", "Byte", "Char", "Double", "Float", "Int", "List", "Long",
            "Map", "MutableList", "MutableMap", "MutableSet", "Nothing", "Pair", "Result",
            "Sequence", "Set", "Short", "String", "Triple", "Unit",
        ),
    ),
    "java" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        keywords = setOf(
            "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char", "class",
            "const", "continue", "default", "do", "double", "else", "enum", "extends", "false",
            "final", "finally", "float", "for", "goto", "if", "implements", "import",
            "instanceof", "int", "interface", "long", "native", "new", "null", "package",
            "private", "protected", "public", "record", "return", "sealed", "short", "static",
            "strictfp", "super", "switch", "synchronized", "this", "throw", "throws", "transient",
            "true", "try", "var", "void", "volatile", "while",
        ),
        types = setOf(
            "Boolean", "Byte", "Character", "Double", "Exception", "Float", "HashMap", "HashSet",
            "Integer", "List", "Long", "Map", "Object", "Optional", "RuntimeException", "Set",
            "Short", "Stream", "String", "StringBuilder", "Thread", "Void",
        ),
    ),
    "bash" to CodeLanguage(
        lineComments = listOf("#"),
        keywords = setOf(
            "alias", "awk", "break", "case", "cat", "cd", "chmod", "chown", "continue", "cp",
            "curl", "declare", "docker", "do", "done", "echo", "elif", "else", "esac", "eval",
            "exec", "exit", "export", "false", "fi", "find", "for", "function", "git", "grep",
            "head", "if", "in", "kill", "ln", "local", "ls", "mkdir", "mv", "printf", "pwd",
            "read", "readonly", "return", "rm", "sed", "set", "shift", "sleep", "source", "sudo",
            "tail", "tar", "then", "time", "touch", "true", "typeset", "unset", "until", "wget",
            "while",
        ),
    ),
    "json" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        quotes = listOf("\""),
        keywords = setOf("true", "false", "null"),
    ),
    "javascript" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        quotes = listOf("\"", "'", "`"),
        keywords = setOf(
            "as", "async", "await", "break", "case", "catch", "class", "const", "continue",
            "debugger", "declare", "default", "delete", "do", "else", "enum", "export", "extends",
            "false", "finally", "for", "from", "function", "get", "if", "implements", "import",
            "in", "instanceof", "interface", "let", "namespace", "new", "null", "of", "private",
            "protected", "public", "readonly", "return", "set", "static", "super", "switch",
            "this", "throw", "true", "try", "type", "typeof", "undefined", "var", "void", "while",
            "with", "yield",
        ),
        types = setOf(
            "any", "bigint", "boolean", "never", "number", "object", "string", "symbol",
            "unknown", "Array", "Date", "Error", "Map", "Partial", "Promise", "Readonly",
            "Record", "Set",
        ),
    ),
    "xml" to CodeLanguage(
        blockComments = listOf("<!--" to "-->"),
        quotes = listOf("\"", "'"),
        tagMarkup = true,
    ),
    "yaml" to CodeLanguage(
        lineComments = listOf("#"),
        keywords = setOf("true", "false", "null", "yes", "no", "on", "off", "~"),
        colonKeys = true,
    ),
    "sql" to CodeLanguage(
        lineComments = listOf("--"),
        blockComments = listOf("/*" to "*/"),
        quotes = listOf("\"", "'", "`"),
        keywords = setOf(
            "all", "alter", "and", "as", "asc", "avg", "begin", "between", "by", "case", "cast",
            "check", "commit", "constraint", "count", "create", "cross", "default", "delete",
            "desc", "distinct", "drop", "else", "end", "exists", "false", "foreign", "from",
            "full", "group", "having", "in", "index", "inner", "insert", "into", "is", "join",
            "key", "left", "like", "limit", "max", "min", "not", "null", "offset", "on", "or",
            "order", "outer", "primary", "references", "right", "rollback", "select", "set",
            "sum", "table", "then", "transaction", "true", "union", "unique", "update", "values",
            "view", "when", "where", "with",
        ),
        types = setOf(
            "bigint", "blob", "boolean", "char", "date", "decimal", "double", "integer", "json",
            "jsonb", "numeric", "real", "serial", "smallint", "text", "timestamp", "uuid",
            "varchar",
        ),
        caseInsensitive = true,
    ),
    "rust" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        keywords = setOf(
            "as", "async", "await", "box", "break", "const", "continue", "crate", "dyn", "else",
            "enum", "Err", "extern", "false", "fn", "for", "if", "impl", "in", "let", "loop",
            "match", "mod", "move", "mut", "None", "Ok", "pub", "ref", "return", "self", "Self",
            "Some", "static", "struct", "super", "trait", "true", "type", "unsafe", "use",
            "where", "while",
        ),
        types = setOf(
            "Arc", "Box", "Cell", "HashMap", "HashSet", "Mutex", "Option", "Path", "PathBuf",
            "Rc", "RefCell", "Result", "String", "Vec", "bool", "char", "f32", "f64", "i8",
            "i16", "i32", "i64", "i128", "isize", "str", "u8", "u16", "u32", "u64", "u128",
            "usize",
        ),
    ),
    "go" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        quotes = listOf("\"", "`", "'"),
        keywords = setOf(
            "break", "case", "chan", "const", "continue", "default", "defer", "else",
            "fallthrough", "false", "for", "func", "go", "goto", "if", "import", "interface",
            "iota", "map", "nil", "package", "range", "return", "select", "struct", "switch",
            "true", "type", "var",
        ),
        types = setOf(
            "any", "bool", "byte", "complex64", "complex128", "error", "float32", "float64",
            "int", "int8", "int16", "int32", "int64", "rune", "string", "uint", "uint8",
            "uint16", "uint32", "uint64", "uintptr",
        ),
    ),
    "c" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        keywords = setOf(
            "auto", "bool", "break", "case", "catch", "char", "class", "const", "constexpr",
            "continue", "default", "delete", "do", "double", "else", "enum", "explicit",
            "extern", "false", "final", "float", "for", "friend", "goto", "if", "inline", "int",
            "long", "mutable", "namespace", "new", "noexcept", "nullptr", "operator", "override",
            "private", "protected", "public", "register", "return", "short", "signed", "sizeof",
            "static", "struct", "switch", "template", "this", "throw", "true", "try", "typedef",
            "typename", "union", "unsigned", "using", "virtual", "void", "volatile", "wchar_t",
            "while",
        ),
        types = CTypes,
    ),
    "cpp" to CodeLanguage(
        lineComments = listOf("//"),
        blockComments = listOf("/*" to "*/"),
        keywords = setOf(
            "auto", "bool", "break", "case", "catch", "char", "class", "const", "constexpr",
            "continue", "default", "delete", "do", "double", "else", "enum", "explicit",
            "extern", "false", "final", "float", "for", "friend", "goto", "if", "inline", "int",
            "long", "mutable", "namespace", "new", "noexcept", "nullptr", "operator", "override",
            "private", "protected", "public", "register", "return", "short", "signed", "sizeof",
            "static", "struct", "switch", "template", "this", "throw", "true", "try", "typedef",
            "typename", "union", "unsigned", "using", "virtual", "void", "volatile", "wchar_t",
            "while",
        ),
        types = CTypes,
    ),
)

internal val CODE_LANGUAGE_ALIASES: Map<String, String> = mapOf(
    "py" to "python", "python" to "python", "python3" to "python",
    "kt" to "kotlin", "kts" to "kotlin", "kotlin" to "kotlin",
    "java" to "java",
    "sh" to "bash", "shell" to "bash", "bash" to "bash", "zsh" to "bash",
    "console" to "bash", "shell-session" to "bash",
    "json" to "json", "jsonc" to "json", "json5" to "json",
    "js" to "javascript", "jsx" to "javascript", "javascript" to "javascript", "node" to "javascript",
    "ts" to "typescript", "tsx" to "typescript", "typescript" to "typescript",
    "xml" to "xml", "html" to "xml", "htm" to "xml", "svg" to "xml", "xhtml" to "xml",
    "yaml" to "yaml", "yml" to "yaml",
    "sql" to "sql",
    "rust" to "rust", "rs" to "rust",
    "go" to "go", "golang" to "go",
    "c" to "c", "h" to "c",
    "cpp" to "cpp", "c++" to "cpp", "cc" to "cpp", "cxx" to "cpp", "hpp" to "cpp", "hxx" to "cpp",
)
