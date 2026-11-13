"""Separate member and administrator page shells, sharing existing tools."""
import html,re,hashlib

def strip_section(source,ident):
 pattern=re.compile(r'<section\b[^>]*\bid="'+re.escape(ident)+r'"[^>]*>',re.S)
 match=pattern.search(source)
 if not match:return source
 depth=1
 for tag in re.finditer(r'</?section\b[^>]*>',source[match.end():],re.S):
  depth+=-1 if tag.group().startswith('</') else 1
  if not depth:return source[:match.start()]+source[match.end()+tag.end():]
 return source

def portal_html(service,admin,user):
 source=(service.root/'web/index.html').read_text(encoding='utf-8');site=html.escape(service.auth.setting('site_name'))
 source=source.replace('<body>','<body data-admin="'+('1' if admin else '0')+'" data-role="'+(user['role'] if user else 'guest')+'" data-authenticated="'+('1' if user else '0')+'">',1)
 source=source.replace('<title>PC28 · 本地研究台</title>','<title>'+site+(' · 管理后台' if admin else '')+'</title>')
 source=source.replace('</head>','<link rel="stylesheet" href="/portal.css"><script src="/account.js" defer></script>'+('<script src="/admin.js" defer></script><script src="/member-admin.js" defer></script><link rel="stylesheet" href="/robot-accounts.css"><script src="/robot-management.js" defer></script><script src="/ai-admin.js" defer></script><link rel="stylesheet" href="/ai-analysis.css">' if admin else '')+'</head>',1)
 navigation=''
 if admin:
  from .permissions import grants,owner,catalog,PAGES
  from .auth import is_main_admin
  access={'user_id':user['id'],'grants':sorted(grants(service.auth,user)),'owner':owner(service.auth,user),'main_admin':is_main_admin(user),'catalog':catalog(),'pages':PAGES}
  import json
  source=source.replace('<body ', '<body data-access-loading="1" data-access="'+html.escape(json.dumps(access,ensure_ascii=False),quote=True)+'" ',1)
  source=source.replace('<script src="/app.js" defer></script>','<script src="/access-admin.js" defer></script><script src="/app.js" defer></script>',1)
  source=source.replace('<button data-tab="home" class="active">首页</button>','',1)
  source=re.sub(r'<details class="nav-menu"><summary>常用工具</summary>.*?</details>','',source,count=1,flags=re.S)
  for ident in ('home','trend','dragon','missing','stats','analysis'):source=strip_section(source,ident)
  source=source.replace('<script src="/tools.js" defer></script>','').replace('LOCAL RESEARCH','ADMIN CONSOLE')
  navigation+="""<span class="admin-nav-group">运行管理</span><button data-tab="admin-overview">后台总览</button><button data-tab="strategies">策略管理</button><button data-tab="money-plans">金额配置管理</button><button data-tab="algorithm-hall">算法大厅</button><button data-tab="simulation">模拟账户</button><button data-tab="history-backtest">历史回测</button><button data-tab="admin-ai">AI概率研究</button><span class="admin-nav-group">站点管理</span><button data-tab="admin-administrators">管理员管理</button><button data-tab="admin-permissions">权限管理</button><button data-tab="admin-users">会员管理</button><button data-tab="admin-member-orders">会员订单明细</button><button data-tab="admin-invites">邀请码管理</button><button data-tab="admin-site">站点设置</button><button data-tab="admin-ops">采集与数据</button><button data-tab="admin-robot">机器人推送</button><button data-tab="admin-audit">安全审计</button>"""
 else:
  for ident in ('strategies','history-backtest','overview','draws','logs','simulation'):source=strip_section(source,ident)
  source=re.sub(r'<dialog\b.*?</dialog>','',source,flags=re.S)
  source=source.replace('<script src="/strategies.js" defer></script>','').replace('<script src="/history-backtest.js" defer></script>','').replace('<script src="/trend-strategy.js" defer></script>','').replace('<script src="/profit-curve.js" defer></script>','')
 if not admin:source=source.replace('<button data-tab="analysis">号码分析</button>','<button data-tab="analysis">号码分析</button><button data-tab="ai-analysis">AI分析</button>',1)
 source=re.sub(r'<details class="nav-menu"><summary>系统管理</summary>.*?</details>',navigation,source,count=1,flags=re.S)
 header=('<span id="account-name">'+html.escape(user['username'])+'</span><button id="account-password" class="secondary">修改密码</button><button id="account-logout" class="secondary">退出</button>') if user else '<button class="secondary" data-open-auth="login">登录</button><button data-open-auth="register">注册</button>'
 source=source.replace('<div class="header-right">','<div class="header-right">'+('' if admin else '<a href="/member">会员中心</a>')+header,1)
 if admin:source=source.replace('<footer>',(service.root/'web/admin-sections.html').read_text(encoding='utf-8')+(service.root/'web/robot-accounts-section.html').read_text(encoding='utf-8')+(service.root/'web/ai-admin-section.html').read_text(encoding='utf-8')+'<footer>',1)
 source=source.replace('</head>','<script src="/income-chart.js" defer></script></head>',1)
 if admin:source=source.replace('</head>','<link rel="stylesheet" href="/algorithm-hall.css"><script src="/algorithm-hall.js" defer></script></head>',1)
 if admin:source=source.replace('</head>','<link rel="stylesheet" href="/money-plans.css"><script src="/money-plans.js" defer></script></head>',1).replace('<footer>',(service.root/'web/money-plans-section.html').read_text(encoding='utf-8')+'<footer>',1)
 if admin:source=source.replace('<footer>',(service.root/'web/algorithm-hall-section.html').read_text(encoding='utf-8')+'<footer>',1)
 if not admin:source=source.replace('</head>','<link rel="stylesheet" href="/ai-analysis.css"><script src="/ai-analysis.js" defer></script></head>',1)
 if not admin:source=source.replace('<footer>',(service.root/'web/ai-analysis-section.html').read_text(encoding='utf-8')+'<footer>',1)
 if user:source=source.replace('</body>',(service.root/'web/account-dialog.html').read_text(encoding='utf-8')+'</body>',1)
 if not admin:
  source=source.replace('</head>','<link rel="stylesheet" href="/member-auth.css"><script src="/member-auth.js" defer></script></head>',1)
  source=source.replace('</body>',(service.root/'web/member-auth-dialog.html').read_text(encoding='utf-8')+'</body>',1)
 if admin:
  source=source.replace('<button id="account-logout"', '<button id="account-mfa" type="button" class="secondary">两步验证</button><button id="account-logout"',1)
  source=source.replace('</head>','<script src="/mfa-login.js" defer></script></head>',1)
  source=source.replace('src="/','src="/admin/assets/').replace('href="/','href="/admin/assets/')
  source=source.replace('</head>','<script src="/admin/assets/access-management.js" defer></script><script src="/admin/assets/admin-bootstrap.js" defer></script></head>',1)
 def asset_version(match):
  path=match.group(2);name=path.rsplit('/',1)[-1];file=service.root/'web'/name
  if not file.is_file():return match.group(0)
  version=hashlib.sha256(file.read_bytes()).hexdigest()[:12]
  return match.group(1)+path+'?v='+version+match.group(3)
 source=re.sub(r'((?:src|href)=")(/[^"?]+\.(?:js|css))(")',asset_version,source)
 return source

def member_center_html(service,user):
 web=service.root/'web'
 authenticated=user is not None and user['role']=='member'
 source=(web/'member-center.html').read_text(encoding='utf-8')
 source=source.replace('data-member-center="1"','data-member-center="1" data-center-scope="1"',1)
 source=source.replace('data-authenticated="0"','data-authenticated="1"' if authenticated else 'data-authenticated="0"')
 source=source.replace('<!-- dialogs -->',(web/('account-dialog.html' if authenticated else 'member-auth-dialog.html')).read_text(encoding='utf-8'))
 script='account.js' if authenticated else 'member-auth.js'
 if authenticated:
  source=source.replace('<button id="account-logout"', '<button id="account-mfa" type="button" class="secondary">两步验证</button><button id="account-logout"',1)
 return source.replace('<!-- auth-script -->',f'<script src="/mfa-login.js" defer></script><script src="/{script}" defer></script>')
