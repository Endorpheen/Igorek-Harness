from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Severity = Literal["critical", "high", "medium", "low"]
Verdict = Literal["safe", "caution", "dangerous"]
TrustLevel = Literal["builtin", "trusted", "community", "agent-created"]


TRUSTED_REPOS = {"openai/skills", "anthropics/skills"}

INSTALL_POLICY = {
    "builtin": ("allow", "allow", "allow"),
    "trusted": ("allow", "allow", "block"),
    "community": ("allow", "block", "block"),
    "agent-created": ("allow", "allow", "ask"),
}

VERDICT_INDEX = {"safe": 0, "caution": 1, "dangerous": 2}
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}

MAX_FILE_COUNT = 50
MAX_TOTAL_SIZE_KB = 1024
MAX_SINGLE_FILE_KB = 256

SCANNABLE_EXTENSIONS = {
    ".md",
    ".txt",
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".rb",
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".conf",
    ".html",
    ".css",
    ".xml",
}

SUSPICIOUS_BINARY_EXTENSIONS = {
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".bin",
    ".dat",
    ".com",
    ".msi",
    ".dmg",
    ".app",
    ".deb",
    ".rpm",
}

INVISIBLE_CHARS = {
    "\u200b": "zero-width space",
    "\u200c": "zero-width non-joiner",
    "\u200d": "zero-width joiner",
    "\u2060": "word joiner",
    "\u2062": "invisible times",
    "\u2063": "invisible separator",
    "\u2064": "invisible plus",
    "\ufeff": "BOM/zero-width no-break space",
    "\u202a": "LTR embedding",
    "\u202b": "RTL embedding",
    "\u202c": "pop directional",
    "\u202d": "LTR override",
    "\u202e": "RTL override",
    "\u2066": "LTR isolate",
    "\u2067": "RTL isolate",
    "\u2068": "first strong isolate",
    "\u2069": "pop directional isolate",
}

THREAT_PATTERNS: tuple[tuple[str, str, Severity, str, str], ...] = (
    (
        r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
        "env_exfil_curl",
        "critical",
        "exfiltration",
        "curl command interpolating secret environment variable",
    ),
    (
        r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)",
        "env_exfil_wget",
        "critical",
        "exfiltration",
        "wget command interpolating secret environment variable",
    ),
    (
        r"requests\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)",
        "env_exfil_requests",
        "critical",
        "exfiltration",
        "requests call with secret variable",
    ),
    (
        r"httpx?\.(get|post|put|patch)\s*\([^\n]*(KEY|TOKEN|SECRET|PASSWORD)",
        "env_exfil_httpx",
        "critical",
        "exfiltration",
        "HTTP client call with secret variable",
    ),
    (
        r"\$HOME/\.ssh|\~/\.ssh|\$HOME/\.aws|\~/\.aws|\$HOME/\.kube|\~/\.kube",
        "credential_store_access",
        "high",
        "exfiltration",
        "references local credential/config directory",
    ),
    (
        r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)",
        "read_secrets_file",
        "critical",
        "exfiltration",
        "reads known secrets file",
    ),
    (
        r"printenv|env\s*\|",
        "dump_all_env",
        "high",
        "exfiltration",
        "dumps all environment variables",
    ),
    (
        r"os\.environ\b(?!\s*\.get\s*\(\s*[\"']PATH)",
        "python_os_environ",
        "high",
        "exfiltration",
        "accesses os.environ",
    ),
    (
        r"os\.getenv\s*\(\s*[^\)]*(?:KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL)",
        "python_getenv_secret",
        "critical",
        "exfiltration",
        "reads secret via os.getenv()",
    ),
    (
        r"ignore\s+(?:\w+\s+)*(previous|all|above|prior)\s+instructions",
        "prompt_injection_ignore",
        "critical",
        "injection",
        "prompt injection: ignore previous instructions",
    ),
    (
        r"do\s+not\s+(?:\w+\s+)*tell\s+(?:\w+\s+)*the\s+user",
        "deception_hide",
        "critical",
        "injection",
        "instructs agent to hide information from user",
    ),
    (
        r"system\s+prompt\s+override|output\s+(?:\w+\s+)*(system|initial)\s+prompt",
        "system_prompt_attack",
        "critical",
        "injection",
        "attempts to override or extract the system prompt",
    ),
    (
        r"<!--[^>]*(?:ignore|override|system|secret|hidden)[^>]*-->",
        "html_comment_injection",
        "high",
        "injection",
        "hidden instructions in HTML comments",
    ),
    (
        r"rm\s+-rf\s+/|rm\s+(-[^\s]*)?r.*\$HOME|\brmdir\s+.*\$HOME",
        "destructive_rm",
        "critical",
        "destructive",
        "destructive recursive delete",
    ),
    (
        r">\s*/etc/|\bmkfs\b|\bdd\s+.*if=.*of=/dev/|truncate\s+-s\s*0\s+/",
        "system_destructive_write",
        "critical",
        "destructive",
        "destructive system write operation",
    ),
    (
        r"shutil\.rmtree\s*\(\s*[\"'/]",
        "python_rmtree",
        "high",
        "destructive",
        "Python rmtree on absolute or root-relative path",
    ),
    (
        r"\bcrontab\b|\.(bashrc|zshrc|profile|bash_profile|zprofile)\b",
        "persistence_shell",
        "medium",
        "persistence",
        "references persistence through cron or shell startup files",
    ),
    (
        r"authorized_keys|/etc/sudoers|visudo|NOPASSWD",
        "privilege_persistence",
        "critical",
        "persistence",
        "references privilege persistence or SSH backdoor files",
    ),
    (
        r"\bnc\s+-[lp]|ncat\s+-[lp]|\bsocat\b|/bin/(ba)?sh\s+-i\s+.*>/dev/tcp/",
        "reverse_shell",
        "critical",
        "network",
        "potential reverse shell",
    ),
    (
        r"\bngrok\b|\blocaltunnel\b|\bserveo\b|\bcloudflared\b",
        "tunnel_service",
        "high",
        "network",
        "uses tunneling service for external access",
    ),
    (
        r"webhook\.site|requestbin\.com|pipedream\.net|hookbin\.com",
        "exfil_service",
        "high",
        "network",
        "references known exfiltration or webhook testing service",
    ),
    (
        r"base64\s+(-d|--decode)\s*\||echo\s+[^\n]*\|\s*(bash|sh|python|perl|ruby|node)",
        "pipe_exec_obfuscation",
        "critical",
        "obfuscation",
        "decodes or pipes text into an interpreter",
    ),
    (
        r"\beval\s*\(\s*[\"']|\bexec\s*\(\s*[\"']|os\.system\s*\(|os\.popen\s*\(",
        "dynamic_shell_execution",
        "high",
        "execution",
        "dynamic code or shell execution",
    ),
    (
        r"subprocess\.(run|call|Popen|check_output)\s*\(",
        "python_subprocess",
        "medium",
        "execution",
        "Python subprocess execution",
    ),
    (
        r"\.\./\.\.|\.\./\.\./\.\.|/etc/passwd|/etc/shadow|/proc/self|/proc/\d+/",
        "path_traversal_or_system_read",
        "high",
        "traversal",
        "path traversal or system file reference",
    ),
    (
        r"curl\s+[^\n]*\|\s*(ba)?sh|wget\s+[^\n]*-O\s*-\s*\|\s*(ba)?sh",
        "download_execute",
        "critical",
        "supply_chain",
        "download-and-execute command",
    ),
    (
        r"pip\s+install\s+(?!-r\s)(?!.*==)|npm\s+install\s+(?!.*@\d)|uv\s+run\s+",
        "unpinned_dependency_execution",
        "medium",
        "supply_chain",
        "unpinned install or uv run",
    ),
    (
        r"(?:api[_-]?key|token|secret|password)\s*[=:]\s*[\"'][A-Za-z0-9+/=_-]{20,}",
        "hardcoded_secret",
        "critical",
        "credential_exposure",
        "possible hardcoded API key, token, or secret",
    ),
    (
        r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----|ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9_]{80,}",
        "embedded_secret",
        "critical",
        "credential_exposure",
        "embedded private key or GitHub token",
    ),
    (
        r"AGENTS\.md|CLAUDE\.md|\.cursorrules|\.clinerules|\.claude/settings|\.codex/config",
        "agent_config_mod",
        "critical",
        "persistence",
        "references persistent agent configuration files",
    ),
)


@dataclass
class Finding:
    pattern_id: str
    severity: Severity
    category: str
    file: str
    line: int
    match: str
    description: str


@dataclass
class ScanResult:
    skill_name: str
    source: str
    trust_level: TrustLevel
    verdict: Verdict
    findings: list[Finding] = field(default_factory=list)
    scanned_at: str = ""
    summary: str = ""
    content_hash: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


def scan_file(file_path: Path, rel_path: str = "") -> list[Finding]:
    if not rel_path:
        rel_path = file_path.name
    if file_path.suffix.lower() not in SCANNABLE_EXTENSIONS and file_path.name != "SKILL.md":
        return []
    try:
        content = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    findings: list[Finding] = []
    lines = content.split("\n")
    seen: set[tuple[str, int]] = set()

    for pattern, pattern_id, severity, category, description in THREAT_PATTERNS:
        for line_number, line in enumerate(lines, start=1):
            if (pattern_id, line_number) in seen:
                continue
            if not re.search(pattern, line, re.IGNORECASE):
                continue
            seen.add((pattern_id, line_number))
            match = line.strip()
            if len(match) > 120:
                match = match[:117] + "..."
            findings.append(
                Finding(
                    pattern_id=pattern_id,
                    severity=severity,
                    category=category,
                    file=rel_path,
                    line=line_number,
                    match=match,
                    description=description,
                )
            )

    for line_number, line in enumerate(lines, start=1):
        for char, name in INVISIBLE_CHARS.items():
            if char not in line:
                continue
            findings.append(
                Finding(
                    pattern_id="invisible_unicode",
                    severity="high",
                    category="injection",
                    file=rel_path,
                    line=line_number,
                    match=f"U+{ord(char):04X} ({name})",
                    description=f"invisible unicode character {name}",
                )
            )
            break

    return findings


def scan_skill(skill_path: Path, source: str = "community") -> ScanResult:
    skill_path = Path(skill_path).expanduser()
    trust_level = _resolve_trust_level(source)
    findings: list[Finding] = []

    if skill_path.is_dir():
        findings.extend(_check_structure(skill_path))
        for file_path in sorted(skill_path.rglob("*")):
            if not file_path.is_file() or file_path.is_symlink():
                continue
            try:
                rel_path = str(file_path.relative_to(skill_path))
            except ValueError:
                rel_path = file_path.name
            findings.extend(scan_file(file_path, rel_path))
        skill_name = skill_path.name
    elif skill_path.is_file():
        findings.extend(scan_file(skill_path, skill_path.name))
        skill_name = skill_path.stem
    else:
        findings.append(
            Finding(
                pattern_id="missing_skill_path",
                severity="critical",
                category="structural",
                file=str(skill_path),
                line=0,
                match="missing path",
                description="skill path does not exist",
            )
        )
        skill_name = skill_path.name or "unknown"

    verdict = _determine_verdict(findings)
    return ScanResult(
        skill_name=skill_name,
        source=source,
        trust_level=trust_level,
        verdict=verdict,
        findings=findings,
        scanned_at=datetime.now(timezone.utc).isoformat(),
        summary=_build_summary(skill_name, source, trust_level, verdict, findings),
        content_hash=content_hash(skill_path),
    )


def should_allow_install(result: ScanResult, force: bool = False) -> tuple[bool | None, str]:
    policy = INSTALL_POLICY.get(result.trust_level, INSTALL_POLICY["community"])
    verdict_index = VERDICT_INDEX.get(result.verdict, 2)
    decision = policy[verdict_index]

    if decision == "allow":
        return True, f"Allowed ({result.trust_level} source, {result.verdict} verdict)"
    if force:
        return True, f"Force-installed despite {result.verdict} verdict ({len(result.findings)} findings)"
    if decision == "ask":
        return None, (
            f"Requires confirmation ({result.trust_level} source + {result.verdict} verdict, "
            f"{len(result.findings)} findings)"
        )
    return False, (
        f"Blocked ({result.trust_level} source + {result.verdict} verdict, "
        f"{len(result.findings)} findings)"
    )


def format_scan_report(result: ScanResult) -> str:
    lines = [
        f"Scan: {result.skill_name} ({result.source}/{result.trust_level})  Verdict: {result.verdict.upper()}"
    ]
    for finding in sorted(result.findings, key=lambda item: SEVERITY_ORDER.get(item.severity, 4)):
        severity = finding.severity.upper().ljust(8)
        category = finding.category.ljust(18)
        location = f"{finding.file}:{finding.line}".ljust(36)
        lines.append(f"  {severity} {category} {location} \"{finding.match[:80]}\"")

    allowed, reason = should_allow_install(result)
    if allowed is True:
        status = "ALLOWED"
    elif allowed is None:
        status = "NEEDS CONFIRMATION"
    else:
        status = "BLOCKED"
    lines.append(f"Decision: {status} — {reason}")
    return "\n".join(lines)


def content_hash(skill_path: Path) -> str:
    digest = hashlib.sha256()
    try:
        if skill_path.is_dir():
            for file_path in sorted(skill_path.rglob("*")):
                if not file_path.is_file() or file_path.is_symlink():
                    continue
                digest.update(str(file_path.relative_to(skill_path)).encode("utf-8"))
                digest.update(b"\0")
                digest.update(file_path.read_bytes())
        elif skill_path.is_file():
            digest.update(skill_path.read_bytes())
    except OSError:
        pass
    return f"sha256:{digest.hexdigest()[:16]}"


def _check_structure(skill_dir: Path) -> list[Finding]:
    findings: list[Finding] = []
    file_count = 0
    total_size = 0
    root = skill_dir.resolve()

    for file_path in sorted(skill_dir.rglob("*")):
        if not file_path.is_file() and not file_path.is_symlink():
            continue
        try:
            rel_path = str(file_path.relative_to(skill_dir))
        except ValueError:
            rel_path = file_path.name
        file_count += 1

        if file_path.is_symlink():
            try:
                resolved = file_path.resolve()
                try:
                    resolved.relative_to(root)
                except ValueError:
                    findings.append(
                        Finding(
                            pattern_id="symlink_escape",
                            severity="critical",
                            category="traversal",
                            file=rel_path,
                            line=0,
                            match=f"symlink -> {resolved}",
                            description="symlink points outside the skill directory",
                        )
                    )
            except OSError:
                findings.append(
                    Finding(
                        pattern_id="broken_symlink",
                        severity="medium",
                        category="traversal",
                        file=rel_path,
                        line=0,
                        match="broken symlink",
                        description="broken or circular symlink",
                    )
                )
            continue

        try:
            stat = file_path.stat()
        except OSError:
            continue
        size = stat.st_size
        total_size += size

        if size > MAX_SINGLE_FILE_KB * 1024:
            findings.append(
                Finding(
                    pattern_id="oversized_file",
                    severity="medium",
                    category="structural",
                    file=rel_path,
                    line=0,
                    match=f"{size // 1024}KB",
                    description=f"file exceeds {MAX_SINGLE_FILE_KB}KB",
                )
            )

        ext = file_path.suffix.lower()
        if ext in SUSPICIOUS_BINARY_EXTENSIONS:
            findings.append(
                Finding(
                    pattern_id="binary_file",
                    severity="critical",
                    category="structural",
                    file=rel_path,
                    line=0,
                    match=f"binary: {ext}",
                    description="binary or executable file should not be in a skill",
                )
            )

        if ext not in {".sh", ".bash", ".py", ".rb", ".pl"} and stat.st_mode & 0o111:
            findings.append(
                Finding(
                    pattern_id="unexpected_executable",
                    severity="medium",
                    category="structural",
                    file=rel_path,
                    line=0,
                    match="executable bit set",
                    description="non-script file has executable permission",
                )
            )

    if file_count > MAX_FILE_COUNT:
        findings.append(
            Finding(
                pattern_id="too_many_files",
                severity="medium",
                category="structural",
                file="(directory)",
                line=0,
                match=f"{file_count} files",
                description=f"skill has more than {MAX_FILE_COUNT} files",
            )
        )

    if total_size > MAX_TOTAL_SIZE_KB * 1024:
        findings.append(
            Finding(
                pattern_id="oversized_skill",
                severity="high",
                category="structural",
                file="(directory)",
                line=0,
                match=f"{total_size // 1024}KB total",
                description=f"skill exceeds {MAX_TOTAL_SIZE_KB}KB total size",
            )
        )

    return findings


def _resolve_trust_level(source: str) -> TrustLevel:
    normalized = str(source or "community").strip()
    for prefix in ("skills-sh/", "skills.sh/", "skils-sh/", "skils.sh/"):
        if normalized.startswith(prefix):
            normalized = normalized[len(prefix):]
            break
    if normalized == "agent-created":
        return "agent-created"
    if normalized == "official" or normalized.startswith("official/"):
        return "builtin"
    if any(normalized == repo or normalized.startswith(repo) for repo in TRUSTED_REPOS):
        return "trusted"
    return "community"


def _determine_verdict(findings: list[Finding]) -> Verdict:
    if not findings:
        return "safe"
    if any(finding.severity == "critical" for finding in findings):
        return "dangerous"
    return "caution"


def _build_summary(
    name: str,
    source: str,
    trust_level: TrustLevel,
    verdict: Verdict,
    findings: list[Finding],
) -> str:
    if not findings:
        return f"{name}: clean scan, no threats detected"
    categories = ", ".join(sorted({finding.category for finding in findings}))
    return (
        f"{name}: {verdict} — {len(findings)} finding(s) in {categories} "
        f"({source}/{trust_level})"
    )
