# 把 ThinFilmForge 发布到 GitHub

这份清单假设你已经在本地把项目跑通（`python -m unittest discover -s tests -t .` 全绿）。
下面所有命令都在**项目根目录**执行。

---

## 0. 一次性准备

```bash
git config --global user.name  "Your Name"
git config --global user.email "you@example.com"
git config --global init.defaultBranch main
```

确认身份 / 凭据：

```bash
git config --list --show-origin | grep user
# HTTPS 方式需要 Personal Access Token；SSH 方式需要已上传公钥
ssh -T git@github.com
```

> 推荐 SSH：`ssh-keygen -t ed25519 -C "you@example.com"`，把 `~/.ssh/id_ed25519.pub`
> 贴到 GitHub → Settings → SSH and GPG keys。之后推送不再需要输入密码。

---

## 1. 初始化本地仓库

```bash
git init -b main
git status                # 确认 .gitignore 生效：不应出现 __pycache__/、*.pyc
```

`git status` 里应该只看到源码、`README.md`、`LICENSE`、`pyproject.toml`、
`examples/`、`tests/`、`docs/`。如果 `__pycache__` 或 `.venv` 出现在列表里，
说明 `.gitignore` 没被识别（检查文件名是否被 Windows 存成了 `.gitignore.txt`）。

---

## 2. 检查 .gitignore 与 LICENSE

- `.gitignore`：已排除字节码、虚拟环境、打包产物、IDE 配置、临时导出。
  若你本地生成了设计结果（如 `out/`、`*.json` 报告），也应在忽略或示例目录中取舍。
- `LICENSE`：MIT，年份与署名可改成你自己的名字。
  两者都应在**第一次提交前**就位，避免历史里出现"先提交后补协议"的尴尬。

自检：

```bash
git check-ignore -v thinfilmforge/__pycache__/x.pyc   # 有输出即被忽略
head -3 LICENSE
```

---

## 3. 首次提交

```bash
git add .
git commit -m "feat: ThinFilmForge 1.0.0 - optical thin-film design workbench

- stable Airy recursion solver (cross-checked against the characteristic matrix)
- hybrid differential-evolution / pattern-search inverse design
- Monte-Carlo yield, spectral envelope and layer sensitivity
- CIE colour, group delay / GDD, angle-resolved map, admittance locus
- zero-dependency CLI, Python API and local web workbench (48 tests)"
```

提交信息建议遵循 Conventional Commits（`feat:` / `fix:` / `docs:` / `test:`），
招聘方翻提交历史时，这比一句 "update" 有说服力得多。

---

## 4. 在 GitHub 建仓库并推送

**网页方式**：GitHub → New repository →
名称 `thinfilm-forge` → **不要**勾选 "Add a README / .gitignore / license"（本地已有）→ Create。

**命令行方式**（已安装 `gh`）：

```bash
gh auth login
gh repo create thinfilm-forge --public --source=. --remote=origin \
   --description "Optical thin-film design & inverse-optimisation workbench (zero dependencies)"
```

然后推送：

```bash
git remote add origin git@github.com:<your-account>/thinfilm-forge.git   # 若上一步未自动添加
git push -u origin main
```

> 如果远程初始化时不小心加了 README，会与本地历史分叉。此时用
> `git pull --rebase origin main` 后再推，**不要** `git push -f` 覆盖远程。

---

## 5. 发布第一个版本

```bash
git tag -a v1.0.0 -m "v1.0.0: first public release

- TMM solver with s/p polarisation, oblique incidence and absorbing media
- inverse design (DE + pattern search) with spectral band targets
- tolerance analysis: yield, envelope, sensitivity
- web workbench, CLI and Python API"
git push origin v1.0.0
```

在 GitHub 仓库 → Releases → Draft a new release：

- Tag 选 `v1.0.0`，标题 `ThinFilmForge v1.0.0`
- 描述里贴 3–4 条亮点 + 一张界面截图
- 可附加 `python -m build` 生成的 `dist/*.whl`、`*.tar.gz`

同时把仓库信息补全（右上角 About 齿轮）：

- Description：`Optical thin-film design & inverse optimisation — TMM, differential evolution, Monte-Carlo yield, zero dependencies`
- Website：留空或填你的博客文章
- Topics：`optics` `thin-film` `optical-coating` `transfer-matrix` `inverse-design`
  `differential-evolution` `python` `photonics` `optoelectronics` `simulation`

---

## 6. 可选但强烈建议

- **CI**：仓库已带 `.github/workflows/tests.yml`，推上去后 Actions 会自动在
  Python 3.9–3.12 上跑单元测试，README 可以再加一个 CI 徽章。
- **README 首屏**：放一张工作台截图（`docs/images/workbench.png`），
  比十行文字更快让人理解你在做什么。
- **Issues 模板 / CONTRIBUTING**：如果打算长期维护再补，个人项目不必强求。
- **GitHub Pages**：本项目的 Web 界面依赖本地 Python 服务，不适合直接上 Pages；
  可以单独做一个静态介绍页，把截图和 GIF 放上去。
- **.gitattributes**：跨平台协作时加一行 `* text=auto eol=lf`，避免 CRLF 噪音。

---

## 7. 常见坑

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 推送被拒 `fetch first` | 远程有你本地没有的提交 | `git pull --rebase origin main` 后再推 |
| `filemode` 变更刷屏 | Windows 与 Linux 权限位差异 | `git config core.filemode false` |
| 中文文件名乱码 | 未开启 UTF-8 路径 | `git config core.quotepath false` |
| 仓库体积暴涨 | 误提交了 `.venv`、截图、构建产物 | 从 `.gitignore` 补规则，`git rm -r --cached <path>` 后重新提交 |
| 大文件 >100 MB 被拒 | GitHub 单文件限制 | 用 Git LFS，或不要提交二进制数据 |
| 提交里带了密钥 | token / 私钥写进代码 | 立刻在平台侧吊销，再用 `git filter-repo` 清理历史 |

---

## 8. 发布后自检清单

- [ ] 从零 clone 到临时目录，按 README 的"安装与运行"能跑通
- [ ] `python -m unittest discover -s tests -t .` 全绿
- [ ] `python run.py serve` 能打开工作台，四个页面都有数据
- [ ] README 里的示例命令可以复制粘贴直接执行
- [ ] Release 页面有 tag、说明和截图
- [ ] 仓库 About 的 description 与 topics 已填写
- [ ] LICENSE 与 README 的许可声明一致
