# Domain / Runtime boundary

The dependency direction is `MCP → Runtime → Domain`. Domain may use Core's
value types, content contracts and pure utilities. Database construction, service
composition, environment-dependent application configuration, document/image
processing, and user content-library filesystem access belong to Runtime.

## Ownership

| Responsibility | Canonical module |
| --- | --- |
| CLI dispatch and persistence workflows | `sagasmith_dnd_runtime.cli` |
| CLI database and dense retrieval construction | `sagasmith_dnd_runtime.bootstrap` |
| Reading source files and attaching package assets | `sagasmith_dnd_runtime.content_assets` |
| PDF portrait extraction | `sagasmith_dnd_runtime.portrait_extraction` |
| Writing content libraries and archives | `sagasmith_dnd_runtime.public_library` |
| Verifying and resolving local official archives | `sagasmith_dnd_runtime.official_library` |
| Content package construction and validation | `sagasmith_dnd.content_packages` |
| Publication rights validation | `sagasmith_dnd.public_library` |
| Official metadata catalog, identity and readiness semantics | `sagasmith_dnd.official_expansions` |
| Rule primitives, edition policies and deterministic state transitions | Domain rule modules |

The local CLI retains its existing trusted-local identity and command semantics.
Moving it does not turn it into an authenticated remote API. Hosted calls continue
through Runtime's identity and command scope.

## Installation and compatibility

Install `sagasmith-dnd-runtime` to obtain the `sagasmith-dnd` console command.
Use Runtime's `documents`, `images`, `embedding`, `vector`, `dense`, or `all` extras
for application features. `sagasmith-dnd` alone remains the rules/content library.
Existing Domain extras remain dependency aliases for compatibility; they do not
install Runtime or create a reverse package dependency.

Old Domain application exports load Runtime only when accessed. They delegate to
the same functions, contain no duplicate implementation, and report the required
installation when Runtime is absent. `python -m sagasmith_dnd.cli` and the old
public-library module command remain available when Runtime is installed.
New code must import the canonical Runtime modules directly.

## Explicit remaining source-compilation boundary

Domain still includes build-time SRD/source compilers and read-only source loaders
(`bundled_rules`, `core_content*`, `content_resolution`, `content_actors`,
`fivetools`) plus the metadata lock loader. These read source material supplied
to the compiler; Domain is therefore not claimed to be a completely I/O-free
package. They do not construct databases, persist campaigns, or depend on Runtime.
The combat engine's internal decomposition is a separate concern from this
application boundary; rule behavior is unchanged by this migration.

## Checks

Domain tests prohibit application/transport imports and import every Domain module
with Runtime blocked. Runtime tests cover legacy exports, console-script ownership,
CLI persistence, content files, archive verification and portrait extraction.
Mixed content integration tests live in Runtime; pure rule tests remain in Domain.
Run the Domain, Runtime and MCP suites in separate pytest processes.

## 中文

Domain 保留规则计算、结构校验、内容身份和发布权限语义；Runtime 接管 CLI、数据库
构建、文件附件、PDF 头像处理和本地内容库读写。旧入口延迟转发到同一个实现，
Domain 不反向依赖 Runtime 安装包。内置来源编译器仍包含只读加载，不能把整个
Domain 包宣称为完全无 I/O；本次拆分不改变战斗规则和本地 CLI 的信任边界。
