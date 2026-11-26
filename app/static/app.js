(function () {
  var token = localStorage.getItem('kp_token') || '';
  var currentIsAdmin = localStorage.getItem('kp_admin') === 'true';
  var currentUsername = localStorage.getItem('kp_user') || '';
  var currentDisplayName = localStorage.getItem('kp_display_name') || currentUsername;
  var currentRole = localStorage.getItem('kp_role') || (currentIsAdmin ? 'admin' : 'operator');
  var currentPermissions = JSON.parse(localStorage.getItem('kp_permissions') || '[]');
  var displayTimezone = document.body.dataset.timezone || 'Asia/Shanghai';
  var passwordPolicyHint = '至少 8 位，并包含大写字母、小写字母、数字和特殊字符（空格不算特殊字符）。';
  function passwordPolicyError(password) {
    var characters = Array.from(password);
    if (characters.length < 8) return '密码至少需要 8 位';
    if (!/[\p{Lu}]/u.test(password)) return '密码需要包含大写字母';
    if (!/[\p{Ll}]/u.test(password)) return '密码需要包含小写字母';
    if (!/[\p{N}]/u.test(password)) return '密码需要包含数字';
    if (!characters.some(function (character) { return /[^\p{L}\p{N}\s]/u.test(character); })) return '密码需要包含特殊字符，空格不算特殊字符';
    return '';
  }
  var pages = {
    dashboard: ['平台总览', '集群运行状态与关键指标'],
    workloads: ['更新发布', '升级镜像、扩缩容、重启和版本回滚'],
    'release-history': ['发布历史', '查看升级与回滚执行状态、事件和容器日志'],
    pods: ['Pod 监控', 'Pod 运行状态与资源消耗'],
    nodes: ['服务器监控', 'Kubernetes 节点 CPU、内存与磁盘'],
    'build-configs': ['构建配置', '复用 Git、Dockerfile 和镜像发布参数'],
    'build-history': ['执行历史', '查看构建状态、日志和产物信息'],
    resources: ['资源管理', '查看、编辑、比较并回退命名空间资源配置'],
    alerts: ['告警规则', '配置阈值、目标范围与通知策略'],
    'alert-events': ['告警事件', '查看告警状态、触发时间并清理历史事件'],
    settings: ['通知渠道', '邮件、飞书、钉钉、企业微信和电话网关'],
    users: ['人员管理', '平台账号、角色与启用状态'],
    'rule-templates': ['规则模板', '复用常见告警阈值配置'],
    'alert-subscriptions': ['告警订阅', '为人员订阅规则和通知对象'],
    incidents: ['故障中心', '告警确认、处理中与恢复状态'],
    'notification-templates': ['通知模板', '统一管理消息标题和正文模板'],
    'notification-records': ['通知记录', '通知发送成功与失败记录'],
    'on-call': ['值班日程', '人员值班时间安排'],
    roles: ['角色管理', '平台角色与权限说明'],
    permissions: ['权限管理', '为测试、研发和运维用户分配可见菜单'],
    'audit-logs': ['日志审计', '查看用户登录及平台操作记录'],
  };
  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return {
        '&': '&amp;',
        '<': '&lt;',
        '>': '&gt;',
        '"': '&quot;',
        "'": '&#39;',
      }[c];
    });
  }
  function toast(message, error) {
    var box = document.getElementById('toast');
    var el = document.createElement('div');
    el.className = 'toast' + (error ? ' error' : '');
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () {
      el.remove();
    }, 4000);
  }
  function errorText(value) {
    if (typeof value === 'string') return value;
    if (Array.isArray(value)) {
      return value.map(function (item) {
        return typeof item === 'string' ? item : (item && (item.msg || item.message || item.detail)) || JSON.stringify(item);
      }).join('；');
    }
    if (value && typeof value === 'object') {
      return value.message || value.msg || value.detail || JSON.stringify(value);
    }
    return String(value || '');
  }
  async function api(path, options) {
    options = options || {};
    options.headers = Object.assign(
      { 'Content-Type': 'application/json' },
      options.headers || {},
    );
    if (token) options.headers.Authorization = 'Bearer ' + token;
    var res = await fetch(path, options);
    if (res.status === 401) {
      logout(false);
      throw new Error('登录已失效');
    }
    var body = await res.json().catch(function () {
      return {};
    });
    if (!res.ok) throw new Error(errorText(body.detail) || '请求失败 ' + res.status);
    return body;
  }
  function statusBadge(value) {
    var c =
      value === 'Running' ||
      value === 'succeeded' ||
      value === 'True' ||
      value === 'firing' ||
      value === '启用'
        ? 'ok'
        : value === 'failed' || value === 'False' || value === 'critical'
          ? 'bad'
          : 'warn';
    return '<span class="badge ' + c + '">' + esc(value) + '</span>';
  }
  var severityLabels = { info: '提示', warning: '警告', critical: '严重' };
  function severityBadge(value) {
    var css = value === 'critical' ? 'bad' : value === 'warning' ? 'warn' : 'ok';
    return '<span class="badge ' + css + '">' + esc(severityLabels[value] || value) + '</span>';
  }
  function formatBytes(v) {
    v = Number(v || 0);
    if (v > 1073741824) return (v / 1073741824).toFixed(1) + ' GiB';
    if (v > 1048576) return (v / 1048576).toFixed(1) + ' MiB';
    return Math.round(v / 1024) + ' KiB';
  }
  function formatDate(value) {
    if (!value) return '-';
    var normalized = String(value).trim();
    if (/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized) ||
        /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(normalized)) {
      normalized = normalized.replace(' ', 'T') + 'Z';
    }
    var date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString('zh-CN', {
      hour12: false,
      timeZone: displayTimezone,
    });
  }
  function formatLogTimestamps(text) {
    if (!document.getElementById('pod-log-timestamps').checked) return text;
    var formatter = new Intl.DateTimeFormat('sv-SE', {
      year: 'numeric', month: '2-digit', day: '2-digit',
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      hourCycle: 'h23', timeZone: displayTimezone,
    });
    var cache = new Map();
    return text.replace(/^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2}))\s/gm, function (_, stamp) {
      if (!cache.has(stamp)) {
        var date = new Date(stamp);
        var milliseconds = stamp.match(/\.(\d+)/);
        cache.set(stamp, Number.isNaN(date.getTime()) ? stamp : formatter.format(date) +
          (milliseconds ? '.' + milliseconds[1].slice(0, 3).padEnd(3, '0') : ''));
      }
      return cache.get(stamp) + ' ';
    });
  }
  function formatClock(value) {
    var date = value instanceof Date ? value : new Date(value);
    return Number.isNaN(date.getTime()) ? '--:--' : date.toLocaleTimeString('zh-CN', {
      hour: '2-digit', minute: '2-digit', hour12: false, timeZone: displayTimezone,
    });
  }
  var pagerStates = {};
  function pageRows(key, rows, render, defaultSize) {
    var state = pagerStates[key] || { page: 1, size: defaultSize || 10 };
    state.render = render;
    pagerStates[key] = state;
    var pages = Math.max(1, Math.ceil(rows.length / state.size));
    state.page = Math.min(Math.max(state.page, 1), pages);
    renderPager(key, rows.length, pages);
    var start = (state.page - 1) * state.size;
    return rows.slice(start, start + state.size);
  }
  function renderPager(key, total, pages) {
    var state = pagerStates[key];
    var target = document.getElementById(key + '-pagination');
    if (!target) return;
    var first = Math.max(1, Math.min(state.page - 2, pages - 4));
    var last = Math.min(pages, first + 4);
    var buttons = '';
    for (var page = first; page <= last; page += 1) {
      buttons +=
        '<button type="button" class="' +
        (page === state.page ? 'active' : '') +
        '" onclick="kpSetPage(\'' +
        key +
        "'," +
        page +
        ')">' +
        page +
        '</button>';
    }
    target.innerHTML =
      '<span>共 ' +
      total +
      ' 条</span><select aria-label="每页数量" onchange="kpSetPageSize(\'' +
      key +
      "',this.value)\">" +
      [10, 20, 50]
        .map(function (size) {
          return (
            '<option value="' +
            size +
            '" ' +
            (size === state.size ? 'selected' : '') +
            '>每页 ' +
            size +
            '</option>'
          );
        })
        .join('') +
      '</select><button type="button" ' +
      (state.page <= 1 ? 'disabled' : '') +
      ' onclick="kpSetPage(\'' +
      key +
      "'," +
      (state.page - 1) +
      ')">上一页</button>' +
      buttons +
      '<button type="button" ' +
      (state.page >= pages ? 'disabled' : '') +
      ' onclick="kpSetPage(\'' +
      key +
      "'," +
      (state.page + 1) +
      ')">下一页</button>';
  }
  window.kpSetPage = function (key, page) {
    var state = pagerStates[key];
    if (!state) return;
    state.page = Number(page);
    state.render();
  };
  window.kpSetPageSize = function (key, size) {
    var state = pagerStates[key];
    if (!state) return;
    state.page = 1;
    state.size = Number(size);
    state.render();
  };
  function persistIdentity(data) {
    localStorage.setItem('kp_admin', String(data.is_admin));
    localStorage.setItem('kp_role', data.role || (data.is_admin ? 'admin' : 'operator'));
    localStorage.setItem('kp_permissions', JSON.stringify(data.menu_permissions || []));
    localStorage.setItem('kp_display_name', data.display_name || data.username || '');
  }
  function firstAllowedPage() {
    var button = Array.from(document.querySelectorAll('nav button[data-page]')).find(function (item) {
      return !item.classList.contains('menu-denied') && !item.classList.contains('hidden');
    });
    return button ? button.dataset.page : '';
  }
  function applyMenuPermissions() {
    var allowed = new Set(currentPermissions);
    document.querySelectorAll('nav button[data-page]').forEach(function (button) {
      var denied = !currentIsAdmin && !allowed.has(button.dataset.page);
      button.classList.toggle('menu-denied', denied);
      button.classList.toggle('hidden', denied);
    });
    document.querySelectorAll('nav .nav-group').forEach(function (group) {
      var hasVisibleChild = Array.from(group.querySelectorAll('button[data-page]')).some(function (button) {
        return !button.classList.contains('hidden');
      });
      group.classList.toggle('permission-hidden', !hasVisibleChild);
    });
  }
  function showApp(user, isAdmin, role, permissions, displayName) {
    document.getElementById('login-view').classList.add('hidden');
    document.getElementById('app-view').classList.remove('hidden');
    currentDisplayName = displayName || localStorage.getItem('kp_display_name') || user || localStorage.getItem('kp_user') || 'admin';
    document.getElementById('current-user').textContent = currentDisplayName;
    var admin = typeof isAdmin === 'boolean' ? isAdmin : localStorage.getItem('kp_admin') === 'true';
    currentIsAdmin = admin;
    currentUsername = user || localStorage.getItem('kp_user') || '';
    currentRole = role || localStorage.getItem('kp_role') || (admin ? 'admin' : 'operator');
    currentPermissions = Array.isArray(permissions) ? permissions : JSON.parse(localStorage.getItem('kp_permissions') || '[]');
    document.querySelectorAll('.admin-only').forEach(function (element) {
      element.classList.toggle('hidden', !admin);
    });
    applyMenuPermissions();
    var firstPage = firstAllowedPage();
    if (firstPage) loadPage(firstPage);
  }
  function clearLoginForm() {
    var form = document.getElementById('login-form');
    form.reset();
    document.getElementById('login-username').value = '';
    document.getElementById('login-password').value = '';
    setTimeout(function () {
      document.getElementById('login-username').value = '';
      document.getElementById('login-password').value = '';
    }, 50);
  }
  function logout(recordAudit) {
    var previousToken = token;
    if (recordAudit !== false && previousToken) {
      fetch('/api/auth/logout', {
        method: 'POST', keepalive: true,
        headers: { Authorization: 'Bearer ' + previousToken, 'Content-Type': 'application/json' },
      }).catch(function () {});
    }
    token = '';
    localStorage.removeItem('kp_token');
    localStorage.removeItem('kp_user');
    localStorage.removeItem('kp_admin');
    localStorage.removeItem('kp_role');
    localStorage.removeItem('kp_permissions');
    localStorage.removeItem('kp_display_name');
    document.getElementById('app-view').classList.add('hidden');
    document.getElementById('login-view').classList.remove('hidden');
    var passwordDialog = document.getElementById('password-change-modal');
    if (passwordDialog.open) passwordDialog.close();
    clearLoginForm();
  }
  function requirePasswordChange(data) {
    document.getElementById('app-view').classList.add('hidden');
    document.getElementById('login-view').classList.remove('hidden');
    var form = document.getElementById('password-change-form');
    form.reset();
    form.dataset.username = data.username;
    form.dataset.isAdmin = String(data.is_admin);
    form.dataset.role = data.role || '';
    form.dataset.permissions = JSON.stringify(data.menu_permissions || []);
    form.dataset.displayName = data.display_name || data.username || '';
    var dialog = document.getElementById('password-change-modal');
    if (!dialog.open) dialog.showModal();
  }
  document
    .getElementById('login-form')
    .addEventListener('submit', async function (e) {
      e.preventDefault();
      try {
        var data = await api('/api/auth/login', {
          method: 'POST',
          body: JSON.stringify({
            username: document.getElementById('login-username').value,
            password: document.getElementById('login-password').value,
          }),
        });
        token = data.access_token;
        localStorage.setItem('kp_token', token);
        localStorage.setItem('kp_user', data.username);
        persistIdentity(data);
        document.getElementById('login-password').value = '';
        if (data.must_change_password) requirePasswordChange(data);
        else showApp(data.username, data.is_admin, data.role, data.menu_permissions, data.display_name);
      } catch (err) {
        toast(err.message, true);
      }
    });
  document.getElementById('logout').onclick = function () { logout(true); };
  document.getElementById('password-change-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var form = event.currentTarget;
    var data = new FormData(form);
    var nextPassword = String(data.get('new_password') || '');
    var policyError = passwordPolicyError(nextPassword);
    if (policyError) {
      toast(policyError, true);
      form.elements.new_password.focus();
      return;
    }
    if (nextPassword !== String(data.get('confirm_password') || '')) {
      toast('两次输入的新密码不一致', true);
      return;
    }
    try {
      await api('/api/auth/change-password', {
        method: 'POST',
        body: JSON.stringify({
          current_password: String(data.get('current_password') || ''),
          new_password: nextPassword,
        }),
      });
      document.getElementById('password-change-modal').close();
      form.reset();
      toast('密码修改成功');
      showApp(form.dataset.username, form.dataset.isAdmin === 'true', form.dataset.role, JSON.parse(form.dataset.permissions || '[]'), form.dataset.displayName);
    } catch (err) {
      toast(err.message, true);
    }
  });
  document.querySelectorAll('nav button[data-page]').forEach(function (btn) {
    btn.onclick = function () {
      loadPage(btn.dataset.page);
    };
  });
  document.querySelectorAll('.nav-group-toggle').forEach(function (btn) {
    btn.onclick = function () {
      btn.closest('.nav-group').classList.toggle('open');
    };
  });
  document.getElementById('refresh').onclick = function () {
    var active = document.querySelector('nav button.active');
    loadPage(active ? active.dataset.page : 'dashboard');
  };
  document.querySelectorAll('[data-open]').forEach(function (btn) {
    btn.onclick = async function () {
      var modal = document.getElementById(btn.dataset.open);
      modal.showModal();
      if (btn.dataset.open === 'deploy-modal') {
        try {
          await prepareDeployModal();
        } catch (err) {
          modal.close();
          toast(err.message, true);
        }
      } else if (btn.dataset.open === 'rule-modal') {
        try {
          await prepareRuleModal();
        } catch (err) {
          modal.close();
          toast(err.message, true);
        }
      } else if (btn.dataset.open === 'channel-modal') {
        try {
          await prepareChannelModal();
        } catch (err) {
          modal.close();
          toast(err.message, true);
        }
      } else if (btn.dataset.open === 'user-modal') {
        prepareUserModal();
      } else if (btn.dataset.open === 'rule-template-modal') {
        prepareRuleTemplateModal();
      } else if (btn.dataset.open === 'subscription-modal') {
        try {
          await prepareSubscriptionModal();
        } catch (err) {
          modal.close();
          toast(err.message, true);
        }
      } else if (btn.dataset.open === 'notification-template-modal') {
        prepareNotificationTemplateModal();
      } else if (btn.dataset.open === 'on-call-modal') {
        try {
          await prepareOnCallModal();
        } catch (err) {
          modal.close();
          toast(err.message, true);
        }
      } else if (btn.dataset.open === 'build-config-modal') {
        prepareBuildConfigModal();
      }
    };
  });
  document.querySelectorAll('[data-close]').forEach(function (btn) {
    btn.onclick = function () {
      btn.closest('dialog').close();
    };
  });
  async function loadPage(name) {
    if (!pages[name]) name = 'dashboard';
    if (!currentIsAdmin && currentPermissions.indexOf(name) === -1) {
      toast('当前账号没有该菜单权限', true);
      name = firstAllowedPage();
      if (!name) return;
    }
    document.querySelectorAll('nav button[data-page]').forEach(function (b) {
      b.classList.toggle('active', b.dataset.page === name);
    });
    var activeButton = document.querySelector('nav button[data-page="' + name + '"]');
    if (activeButton && activeButton.closest('.nav-group')) {
      activeButton.closest('.nav-group').classList.add('open');
    }
    document.querySelectorAll('.page').forEach(function (p) {
      p.classList.toggle('active', p.id === 'page-' + name);
    });
    document.getElementById('page-title').textContent = pages[name][0];
    document.getElementById('page-subtitle').textContent = pages[name][1];
    try {
      if (name === 'dashboard') await loadDashboard();
      if (name === 'workloads') await loadDeployments();
      if (name === 'release-history') await loadReleaseHistory();
      if (name === 'pods') await loadPods();
      if (name === 'nodes') await loadNodes();
      if (name === 'build-configs' || name === 'build-history') await loadBuilds();
      if (name === 'resources') await loadResourceManager();
      if (name === 'alerts') await loadAlerts();
      if (name === 'alert-events') await loadAlertEvents();
      if (name === 'settings') await loadChannels();
      if (name === 'users') await loadUsers();
      if (name === 'rule-templates') await loadRuleTemplates();
      if (name === 'alert-subscriptions') await loadSubscriptions();
      if (name === 'incidents') await loadIncidents();
      if (name === 'notification-templates') await loadNotificationTemplates();
      if (name === 'notification-records') await loadNotificationRecords();
      if (name === 'on-call') await loadOnCall();
      if (name === 'roles') await loadRoles();
      if (name === 'permissions') await loadPermissions();
      if (name === 'audit-logs') await loadAuditLogs();
    } catch (err) {
      toast(err.message, true);
    }
  }
  async function loadDashboard() {
    var data = await api('/api/overview');
    var cards = [
      ['节点', data.nodes_ready + '/' + data.nodes],
      ['Pod', data.pods_running + '/' + data.pods],
      ['Deployment', data.deployments],
      ['构建中', data.builds_running],
      ['告警中', data.alerts_firing],
    ];
    document.getElementById('overview-cards').innerHTML = cards
      .map(function (x) {
        return (
          '<div class="card"><span>' +
          esc(x[0]) +
          '</span><b>' +
          esc(x[1]) +
          '</b></div>'
        );
      })
      .join('');
    var alerts = await api('/api/alerts?status=firing&limit=10');
    document.getElementById('dashboard-alerts').innerHTML = alerts.length
      ? alerts.map(eventItem).join('')
      : '<p class="muted">当前没有触发中的告警</p>';
  }
  var allDeployments = [];
  var deploymentNamespaceFilter = document.getElementById('deployment-namespace-filter');
  async function loadDeployments() {
    var data = await Promise.all([api('/api/deployments'), api('/api/namespaces')]);
    allDeployments = data[0];
    var current = deploymentNamespaceFilter.value;
    replaceOptions(deploymentNamespaceFilter, [{ value: '', label: '全部命名空间' }].concat(
      data[1].map(function (namespace) { return { value: namespace, label: namespace }; }),
    ));
    deploymentNamespaceFilter.value = data[1].indexOf(current) >= 0 ? current : '';
    renderDeployments();
  }
  function renderDeployments() {
    var selectedNamespace = deploymentNamespaceFilter.value;
    var filtered = selectedNamespace
      ? allDeployments.filter(function (item) { return item.namespace === selectedNamespace; })
      : allDeployments;
    var rows = pageRows('deployments', filtered, renderDeployments, 10);
    document.getElementById('deployments-body').innerHTML = rows
      .map(function (x) {
        return (
          '<tr><td>' +
          esc(x.namespace) +
          '</td><td><strong>' +
          esc(x.name) +
          '</strong></td><td title="' +
          esc(x.image) +
          '">' +
          esc(x.image) +
          '</td><td>' +
          x.replicas +
          '</td><td>' +
          x.ready_replicas +
          '/' +
          x.replicas +
          '</td><td>' +
          x.revision +
          '</td><td><div class="actions"><button class="action-success" onclick="kpRestart(\'' +
          esc(x.namespace) +
          "','" +
          esc(x.name) +
          '\')">重启</button><button class="action-edit" onclick="kpScale(\'' +
          esc(x.namespace) +
          "','" +
          esc(x.name) +
          "'," +
          x.replicas +
          ')">扩缩容</button><button class="action-warn" onclick="kpRollback(\'' +
          esc(x.namespace) +
          "','" +
          esc(x.name) +
          '\')">回滚</button></div></td></tr>'
        );
      })
      .join('') || '<tr><td colspan="7" class="muted">暂无 Deployment</td></tr>';
  }
  deploymentNamespaceFilter.addEventListener('change', function () {
    if (pagerStates.deployments) pagerStates.deployments.page = 1;
    renderDeployments();
  });
  var deployOptions = {
    deployments: [],
    builds: [],
  };
  var deployForm = document.getElementById('deploy-form');
  var namespaceSelect = deployForm.elements.namespace;
  var applicationSelect = deployForm.elements.name;
  var imageSelect = deployForm.elements.image;
  function replaceOptions(select, options) {
    select.replaceChildren();
    options.forEach(function (item) {
      select.add(new Option(item.label, item.value));
    });
  }
  function imageBelongsTo(image, namespace, application) {
    var repository = String(image || '').split('@')[0].replace(/:[^/:]+$/, '');
    var segments = repository.toLowerCase().split('/');
    var app = application.toLowerCase();
    var ns = namespace.toLowerCase();
    return (
      segments[segments.length - 1] === app ||
      repository.toLowerCase().indexOf('/' + ns + '/' + app) >= 0 ||
      repository.toLowerCase().indexOf('/' + app + '-') >= 0 ||
      repository.toLowerCase().indexOf('/' + app + '_') >= 0
    );
  }
  function deploymentForSelection() {
    return deployOptions.deployments.find(function (item) {
      return (
        item.namespace === namespaceSelect.value &&
        item.name === applicationSelect.value
      );
    });
  }
  function addImageCandidate(target, seen, value, source) {
    if (!value || seen.has(value)) return;
    seen.add(value);
    target.push({ value: value, label: value + '  ·  ' + source });
  }
  function populateImages(deployment) {
    var namespace = namespaceSelect.value;
    var application = applicationSelect.value;
    var options = [];
    var seen = new Set();
    if (deployment) {
      addImageCandidate(options, seen, deployment.image, '当前运行');
    }
    if (application) {
      deployOptions.builds.forEach(function (build) {
        if (
          (build.status === 'succeeded' || build.status === 'success') &&
          imageBelongsTo(build.destination, namespace, application)
        ) {
          addImageCandidate(options, seen, build.destination, '构建 #' + build.id);
        }
      });
    }
    if (!options.length) {
      options.push({ value: '', label: '暂无自动关联的镜像' });
    }
    options.push({ value: '__custom__', label: '手工输入镜像地址...' });
    replaceOptions(imageSelect, options);
    var currentImage = deployment && deployment.image ? deployment.image : '';
    if (currentImage && Array.from(imageSelect.options).some(function (option) { return option.value === currentImage; })) {
      imageSelect.value = currentImage;
    } else if (currentImage) {
      imageSelect.value = '__custom__';
      deployForm.elements.image_custom.value = currentImage;
    } else {
      imageSelect.value = options[0].value;
    }
    updateCustomImageField();
  }
  function updateCustomImageField() {
    var field = document.getElementById('custom-image-field');
    var input = deployForm.elements.image_custom;
    var custom = imageSelect.value === '__custom__';
    field.classList.toggle('hidden', !custom);
    input.required = custom;
    if (custom) input.focus();
  }
  function populateDeploymentDetails() {
    var deployment = deploymentForSelection();
    populateImages(deployment);
  }
  function populateApplications() {
    var namespace = namespaceSelect.value;
    var applications = deployOptions.deployments
      .filter(function (item) {
        return item.namespace === namespace;
      })
      .sort(function (a, b) {
        return a.name.localeCompare(b.name);
      })
      .map(function (item) {
        return { value: item.name, label: item.name };
      });
    if (!applications.length) {
      applications.push({ value: '', label: '该命名空间暂无 Deployment' });
    }
    replaceOptions(applicationSelect, applications);
    populateDeploymentDetails();
  }
  async function prepareDeployModal() {
    replaceOptions(namespaceSelect, [{ value: '', label: '正在加载...' }]);
    replaceOptions(applicationSelect, [{ value: '', label: '请稍候...' }]);
    replaceOptions(imageSelect, [{ value: '', label: '请稍候...' }]);
    var data = await Promise.all([
      api('/api/namespaces'),
      api('/api/deployments'),
      api('/api/builds?limit=500'),
    ]);
    deployOptions.deployments = data[1];
    deployOptions.builds = data[2];
    var namespaces = data[0].map(function (name) {
      return { value: name, label: name };
    });
    replaceOptions(namespaceSelect, namespaces);
    var preferred = data[1].length ? data[1][0].namespace : 'default';
    if (data[0].indexOf(preferred) >= 0) namespaceSelect.value = preferred;
    populateApplications();
  }
  namespaceSelect.addEventListener('change', populateApplications);
  applicationSelect.addEventListener('change', populateDeploymentDetails);
  imageSelect.addEventListener('change', updateCustomImageField);
  window.kpRestart = async function (ns, name) {
    if (!confirm('确认滚动重启 ' + ns + '/' + name + '？')) return;
    try {
      await api(
        '/api/deployments/' +
          encodeURIComponent(ns) +
          '/' +
          encodeURIComponent(name) +
          '/restart',
        { method: 'POST' },
      );
      toast('已触发滚动重启');
      loadDeployments();
    } catch (e) {
      toast(e.message, true);
    }
  };
  window.kpScale = async function (ns, name, current) {
    var v = prompt('请输入目标副本数', current);
    if (v === null) return;
    try {
      await api(
        '/api/deployments/' +
          encodeURIComponent(ns) +
          '/' +
          encodeURIComponent(name) +
          '/scale',
        { method: 'POST', body: JSON.stringify({ replicas: Number(v) }) },
      );
      toast('扩缩容已提交');
      loadDeployments();
    } catch (e) {
      toast(e.message, true);
    }
  };
  window.kpRollback = async function (ns, name) {
    var dialog = document.getElementById('rollback-modal');
    var form = document.getElementById('rollback-form');
    form.elements.namespace.value = ns;
    form.elements.name.value = name;
    replaceOptions(form.elements.revision, [{ value: '', label: '正在加载可回滚版本...' }]);
    document.getElementById('rollback-current').textContent = ns + '/' + name;
    document.getElementById('rollback-version-detail').innerHTML = '';
    dialog.showModal();
    try {
      var data = await api(
        '/api/deployments/' +
          encodeURIComponent(ns) +
          '/' +
          encodeURIComponent(name) + '/revisions',
      );
      rollbackRevisionOptions = data.items || [];
      document.getElementById('rollback-current').textContent =
        ns + '/' + name + ' · 当前 revision ' + data.current_revision;
      replaceOptions(
        form.elements.revision,
        rollbackRevisionOptions.length
          ? rollbackRevisionOptions.map(function (item) {
              return {
                value: String(item.revision),
                label: 'revision ' + item.revision + ' · ' + item.image + ' · ' + formatDate(item.created_at),
              };
            })
          : [{ value: '', label: '没有可回滚的历史版本' }],
      );
      form.elements.revision.disabled = !rollbackRevisionOptions.length;
      form.querySelector('button[type="submit"]').disabled = !rollbackRevisionOptions.length;
      renderRollbackVersionDetail();
    } catch (e) {
      dialog.close();
      toast(e.message, true);
    }
  };
  var rollbackRevisionOptions = [];
  function renderRollbackVersionDetail() {
    var value = Number(document.getElementById('rollback-form').elements.revision.value);
    var item = rollbackRevisionOptions.find(function (row) { return row.revision === value; });
    document.getElementById('rollback-version-detail').innerHTML = item
      ? '<strong>revision ' + item.revision + '</strong><span>' + esc(item.image) +
        '</span><small>创建于 ' + esc(formatDate(item.created_at)) + ' · 当时副本 ' + item.replicas +
        ' · 就绪 ' + item.ready_replicas + '</small>'
      : '<span class="muted">请选择一个仍保留在集群中的历史版本</span>';
  }
  document.getElementById('rollback-form').elements.revision.addEventListener('change', renderRollbackVersionDetail);
  document.getElementById('rollback-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var form = e.target;
    try {
      var data = await api(
        '/api/deployments/' + encodeURIComponent(form.elements.namespace.value) + '/' +
          encodeURIComponent(form.elements.name.value) + '/rollback',
        { method: 'POST', body: JSON.stringify({ revision: Number(form.elements.revision.value) }) },
      );
      form.closest('dialog').close();
      toast('回滚已提交');
      openReleaseProgress(data.execution_id);
      loadDeployments();
    } catch (err) {
      toast(err.message, true);
    }
  });
  document
    .getElementById('deploy-form')
    .addEventListener('submit', async function (e) {
      e.preventDefault();
      var f = new FormData(e.target);
      var applicationName = f.get('name');
      var image = f.get('image');
      if (image === '__custom__') image = f.get('image_custom');
      var p = {
        namespace: f.get('namespace'),
        name: applicationName,
        image: image,
      };
      try {
        var data = await api('/api/deployments', {
          method: 'POST',
          body: JSON.stringify(p),
        });
        e.target.closest('dialog').close();
        toast('镜像升级已提交');
        openReleaseProgress(data.execution_id);
        loadDeployments();
      } catch (err) {
        toast(err.message, true);
      }
    });

  var releaseHistoryRows = [];
  var activeReleaseExecution = null;
  var releasePollTimer = null;
  var releaseStatusLabels = {
    submitted: '历史记录', running: '进行中', succeeded: '已完成', failed: '失败',
  };
  function releaseStatusBadge(status) {
    var css = status === 'succeeded' ? 'ok' : status === 'failed' ? 'bad' : 'warn';
    return '<span class="badge ' + css + '">' + esc(releaseStatusLabels[status] || status) + '</span>';
  }
  async function loadReleaseHistory() {
    releaseHistoryRows = await api('/api/deployment-history?limit=500');
    renderReleaseHistory();
  }
  function renderReleaseHistory() {
    var rows = pageRows('release-history', releaseHistoryRows, renderReleaseHistory, 10);
    document.getElementById('release-history-body').innerHTML = rows.map(function (item) {
      return '<tr><td>#' + item.id + '</td><td>' + esc(formatDate(item.created_at)) +
        '</td><td><strong>' + esc(item.namespace + '/' + item.workload) +
        '</strong></td><td class="release-image-cell" title="' + esc(item.image) + '">' + esc(item.image) +
        '</td><td>' + esc(item.strategy === 'Rollback' ? '回滚' : '滚动升级') +
        '</td><td>' + esc(item.revision || '-') + '</td><td>' + releaseStatusBadge(item.status) +
        '</td><td>' + esc(item.operator) + '</td><td><button class="action-info" onclick="kpViewRelease(' +
        item.id + ')">查看状态</button></td></tr>';
    }).join('') || '<tr><td colspan="9" class="muted">暂无发布记录</td></tr>';
  }
  window.kpRefreshReleaseHistory = loadReleaseHistory;
  window.kpViewRelease = function (executionId) {
    openReleaseProgress(executionId);
  };
  function openReleaseProgress(executionId) {
    activeReleaseExecution = executionId;
    var dialog = document.getElementById('release-progress-modal');
    document.getElementById('release-progress-title').textContent = '发布执行 #' + executionId;
    document.getElementById('release-live-state').textContent = '正在获取状态...';
    document.getElementById('release-progress-summary').innerHTML = '';
    document.getElementById('release-pods').innerHTML = '<p class="muted">正在加载 Pod 状态...</p>';
    document.getElementById('release-events').innerHTML = '<p class="muted">正在加载事件...</p>';
    document.getElementById('release-container-logs').textContent = '正在加载容器日志...';
    if (!dialog.open) dialog.showModal();
    if (releasePollTimer) clearInterval(releasePollTimer);
    refreshReleaseProgress();
    releasePollTimer = setInterval(refreshReleaseProgress, 2000);
  }
  async function refreshReleaseProgress() {
    if (!activeReleaseExecution) return;
    try {
      var data = await api('/api/deployment-history/' + activeReleaseExecution + '/status');
      document.getElementById('release-progress-title').textContent =
        (data.strategy === 'Rollback' ? '版本回滚' : '镜像升级') + ' #' + data.execution_id;
      document.getElementById('release-live-state').innerHTML =
        releaseStatusBadge(data.status) + (data.status === 'running' ? '<span class="live-dot"></span>实时刷新' : '');
      document.getElementById('release-progress-summary').innerHTML =
        '<div><span>应用</span><strong>' + esc(data.namespace + '/' + data.name) + '</strong></div>' +
        '<div><span>Revision</span><strong>' + esc(data.revision || '-') + '</strong></div>' +
        '<div><span>副本进度</span><strong>' + data.ready_replicas + '/' + data.desired_replicas + '</strong></div>' +
        '<div><span>已更新 / 可用</span><strong>' + data.updated_replicas + ' / ' + data.available_replicas + '</strong></div>' +
        '<div class="wide"><span>目标镜像</span><strong>' + esc(data.expected_image || data.image) + '</strong></div>';
      document.getElementById('release-pods').innerHTML = (data.pods || []).map(function (pod) {
        return '<div class="release-feed-item"><div><strong>' + esc(pod.name) + '</strong>' +
          statusBadge(pod.phase) + '</div><small>就绪 ' + pod.ready + '/' + pod.containers +
          ' · 重启 ' + pod.restarts + ' · ' + esc(formatDate(pod.created_at)) + '</small></div>';
      }).join('') || '<p class="muted">尚未创建新 Pod</p>';
      document.getElementById('release-events').innerHTML = (data.events || []).map(function (event) {
        return '<div class="release-feed-item ' + (event.type === 'Warning' ? 'is-warning' : '') +
          '"><div><strong>' + esc(event.reason || event.type) + '</strong><span>' +
          esc(event.object) + '</span></div><p>' + esc(event.message) + '</p><small>' +
          esc(formatDate(event.created_at)) + '</small></div>';
      }).join('') || '<p class="muted">暂无相关事件</p>';
      document.getElementById('release-container-logs').textContent =
        data.container_logs || '新 Pod 暂无可读取的容器日志。';
      var log = document.getElementById('release-container-logs');
      log.scrollTop = log.scrollHeight;
      if (data.status === 'succeeded' || data.status === 'failed') {
        clearInterval(releasePollTimer);
        releasePollTimer = null;
        loadDeployments();
        if (document.getElementById('page-release-history').classList.contains('active')) loadReleaseHistory();
      }
    } catch (err) {
      document.getElementById('release-live-state').textContent = err.message;
    }
  }
  document.getElementById('release-refresh').onclick = refreshReleaseProgress;
  document.getElementById('release-progress-modal').addEventListener('close', function () {
    if (releasePollTimer) clearInterval(releasePollTimer);
    releasePollTimer = null;
    activeReleaseExecution = null;
  });
  var allPods = [];
  var podLogTimer = null;
  var podLogRequestId = 0;
  var podLogState = null;
  async function loadPods() {
    allPods = await api('/api/pods');
    renderPods();
  }
  function renderPods() {
    var q = document.getElementById('pod-filter').value.toLowerCase();
    var rows = allPods.filter(function (x) {
      return (x.namespace + '/' + x.name).toLowerCase().indexOf(q) >= 0;
    });
    rows = pageRows('pods', rows, renderPods, 10);
    document.getElementById('pods-body').innerHTML = rows
      .map(function (x) {
        var restartButton = x.can_restart === false
          ? '<button class="action-warn" disabled title="该 Pod 没有可用控制器，或已完成的 Job 不支持重启">重启</button>'
          : '<button class="action-warn" onclick="kpRestartPod(\'' + esc(x.namespace) + '\',\'' + esc(x.name) + '\')">重启</button>';
        return (
          '<tr><td>' +
          esc(x.namespace) +
          '</td><td>' +
          esc(x.name) +
          '</td><td>' +
          statusBadge(x.phase) +
          '</td><td>' +
          x.ready +
          '</td><td>' +
          x.restarts +
          '</td><td>' +
          x.cpu_percent.toFixed(1) +
          '%</td><td>' +
          x.memory_percent.toFixed(1) +
          '% (' +
          formatBytes(x.memory_used) +
          ')</td><td>' +
          esc(x.node) +
          '</td><td><div class="actions"><button class="action-info" onclick="kpPodLog(\'' + esc(x.namespace) + '\',\'' + esc(x.name) + '\')">日志</button>' +
          restartButton + '</div></td></tr>'
        );
      })
      .join('') || '<tr><td colspan="8" class="muted">暂无匹配的 Pod</td></tr>';
  }
  document.getElementById('pod-filter').oninput = function () {
    if (pagerStates.pods) pagerStates.pods.page = 1;
    renderPods();
  };
  window.kpRestartPod = async function (namespace, name) {
    if (!confirm('确认重启 Pod “' + name + '”？平台会删除当前 Pod，由 Deployment、StatefulSet、DaemonSet 等控制器自动重建。')) return;
    try {
      var result = await api('/api/pods/' + encodeURIComponent(namespace) + '/' + encodeURIComponent(name) + '/restart', {
        method: 'POST',
      });
      toast('已请求重启，' + result.controller_kind + ' 控制器正在重建 Pod');
      setTimeout(function () { loadPods().catch(function () {}); }, 1500);
    } catch (err) { toast(err.message, true); }
  };
  function stopPodLogPolling() {
    if (podLogTimer) clearInterval(podLogTimer);
    podLogTimer = null;
  }
  function startPodLogPolling() {
    stopPodLogPolling();
    if (!podLogState || !document.getElementById('pod-log-follow').checked) return;
    podLogTimer = setInterval(function () {
      refreshPodLog(false).catch(function () {});
    }, 2000);
  }
  function podLogVisibleText() {
    var raw = (podLogState && podLogState.logs) || '';
    if (podLogState && podLogState.clearBaseline) {
      if (raw.startsWith(podLogState.clearBaseline)) {
        raw = raw.slice(podLogState.clearBaseline.length);
      } else if (podLogState.clearAnchor) {
        var anchorIndex = raw.lastIndexOf(podLogState.clearAnchor);
        if (anchorIndex >= 0) raw = raw.slice(anchorIndex + podLogState.clearAnchor.length).replace(/^\r?\n/, '');
      }
    }
    raw = formatLogTimestamps(raw);
    var query = document.getElementById('pod-log-filter').value.trim().toLowerCase();
    if (!query) return raw;
    return raw.split(/\r?\n/).filter(function (line) {
      return line.toLowerCase().indexOf(query) >= 0;
    }).join('\n');
  }
  function renderPodLogText(forceScroll) {
    var content = document.getElementById('pod-log-content');
    var text = podLogVisibleText();
    var lines = text ? text.split(/\r?\n/).length : 0;
    var query = document.getElementById('pod-log-filter').value.trim();
    content.textContent = text || (query ? '没有匹配的日志' : '暂无日志');
    document.getElementById('pod-log-match-count').textContent =
      lines + ' 行' + (query ? ' · 已筛选' : '');
    if (forceScroll || document.getElementById('pod-log-autoscroll').checked) {
      requestAnimationFrame(function () { content.scrollTop = content.scrollHeight; });
    }
  }
  function updatePodLogContainers(items, selected) {
    var select = document.getElementById('pod-log-container');
    var labels = { container: '容器', init: 'Init 容器', ephemeral: '临时容器' };
    var groups = ['container', 'init', 'ephemeral'];
    select.innerHTML = groups.map(function (type) {
      var options = items.filter(function (item) { return item.type === type; }).map(function (item) {
        return '<option value="' + esc(item.name) + '">' + esc(item.name) +
          (item.restart_count ? ' · 重启 ' + item.restart_count : '') + '</option>';
      }).join('');
      return options ? '<optgroup label="' + labels[type] + '">' + options + '</optgroup>' : '';
    }).join('');
    var active = selected || (items[0] && items[0].name) || '';
    select.value = active;
    select.disabled = false;
    var current = items.find(function (item) { return item.name === active; });
    var previous = document.getElementById('pod-log-previous');
    previous.disabled = !current || !current.restart_count;
    if (previous.disabled) previous.checked = false;
    previous.title = previous.disabled ? '该容器没有重启记录' : '查看该容器的上一实例日志';
  }
  async function refreshPodLog(manual, force) {
    if (!podLogState) return;
    if (podLogState.loading && !force) return;
    if (force) podLogRequestId += 1;
    var requestId = ++podLogRequestId;
    var container = document.getElementById('pod-log-container').value;
    // A Pod can be replaced between row render and opening the dialog. Do not
    // send a stale container name left by the previous Pod; the API response
    // will populate the valid container list for the current Pod.
    if (!podLogState.containers.some(function (item) { return item.name === container; })) {
      container = '';
    }
    var params = new URLSearchParams({
      tail_lines: document.getElementById('pod-log-lines').value,
      since_seconds: document.getElementById('pod-log-since').value,
      previous: String(document.getElementById('pod-log-previous').checked),
      timestamps: String(document.getElementById('pod-log-timestamps').checked),
    });
    if (container) params.set('container', container);
    podLogState.loading = true;
    document.getElementById('pod-log-live-state').textContent = '● 正在读取';
    try {
      var data = await api('/api/pods/' + encodeURIComponent(podLogState.namespace) + '/' +
        encodeURIComponent(podLogState.name) + '/logs?' + params.toString());
      if (!podLogState || requestId !== podLogRequestId) return;
      podLogState.logs = data.logs || '';
      podLogState.containers = data.containers || [];
      updatePodLogContainers(podLogState.containers, data.selected_container);
      renderPodLogText(manual);
      var now = new Date();
      document.getElementById('pod-log-updated-at').textContent =
        '更新于 ' + now.toLocaleTimeString('zh-CN', { hour12: false, timeZone: displayTimezone });
      document.getElementById('pod-log-live-state').textContent =
        document.getElementById('pod-log-follow').checked ? '● 实时更新中' : '● 已暂停';
    } catch (err) {
      if (!podLogState || requestId !== podLogRequestId) return;
      document.getElementById('pod-log-live-state').textContent = '● 读取失败';
      if (manual) toast(err.message, true);
    } finally {
      if (podLogState && requestId === podLogRequestId) podLogState.loading = false;
    }
  }
  function reloadPodLog() {
    if (!podLogState) return;
    podLogRequestId += 1;
    podLogState.loading = false;
    podLogState.logs = '';
    podLogState.clearBaseline = '';
    podLogState.clearAnchor = '';
    refreshPodLog(true).catch(function () {});
  }
  window.kpPodLog = async function (namespace, name) {
    stopPodLogPolling();
    podLogRequestId += 1;
    podLogState = {
      namespace: namespace, name: name, logs: '', containers: [], loading: false,
      clearBaseline: '', clearAnchor: '',
    };
    document.getElementById('pod-log-title').textContent = 'Pod 日志 · ' + name;
    document.getElementById('pod-log-live-state').textContent = '● 正在连接';
    document.getElementById('pod-log-filter').value = '';
    document.getElementById('pod-log-previous').checked = false;
    document.getElementById('pod-log-follow').checked = true;
    document.getElementById('pod-log-content').textContent = '正在读取日志...';
    document.getElementById('pod-log-match-count').textContent = '0 行';
    document.getElementById('pod-log-updated-at').textContent = '尚未刷新';
    var containerSelect = document.getElementById('pod-log-container');
    containerSelect.innerHTML = '<option value="">正在读取容器...</option>';
    containerSelect.value = '';
    containerSelect.disabled = true;
    var modal = document.getElementById('pod-log-modal');
    modal.classList.remove('is-log-expanded');
    document.getElementById('pod-log-expand').textContent = '放大日志区';
    modal.showModal();
    await refreshPodLog(true);
    startPodLogPolling();
  };
  var allNodes = [];
  async function loadNodes() {
    allNodes = await api('/api/nodes');
    renderNodes();
  }
  function renderNodes() {
    var rows = pageRows('nodes', allNodes, renderNodes, 10);
    document.getElementById('nodes-grid').innerHTML = rows
      .map(function (x) {
        var disks = (x.disk_mounts || []).map(function (disk) {
          return '<div class="metric-row"><div class="metric-label"><span>磁盘 ' + esc(disk.path) + '</span><b>' +
            Number(disk.percent || 0).toFixed(1) + '%</b></div><div class="progress"><i style="width:' +
            Math.min(Number(disk.percent || 0), 100) + '%"></i></div><div class="node-disk-meta">' +
            formatBytes(disk.used || 0) + ' / ' + formatBytes(disk.total || 0) + '</div></div>';
        }).join('') || '<div class="node-disk-empty">磁盘数据等待节点采集</div>';
        return (
          '<div class="node-card"><h3>' +
          esc(x.name) +
          ' ' +
          statusBadge(x.ready) +
          '</h3><div class="node-meta">' +
          esc(x.internal_ip) +
          ' · ' +
          esc(x.kubelet_version) +
          ' · ' +
          esc(x.os_image) +
          '</div><div class="metric-row"><div class="metric-label"><span>CPU</span><b>' +
          x.cpu_percent.toFixed(1) +
          '%</b></div><div class="progress"><i style="width:' +
          Math.min(x.cpu_percent, 100) +
          '%"></i></div></div><div class="metric-row"><div class="metric-label"><span>内存</span><b>' +
          x.memory_percent.toFixed(1) +
          '%</b></div><div class="progress"><i style="width:' +
          Math.min(x.memory_percent, 100) +
          '%"></i></div></div>' + disks + '</div>'
        );
      })
      .join('') || '<p class="muted">暂无节点</p>';
  }
  document.getElementById('disk-config-button').addEventListener('click', async function () {
    try {
      var data = await api('/api/node-disk-config');
      document.getElementById('disk-config-form').elements.paths.value = (data.paths || ['/']).join('\n');
      document.getElementById('disk-config-modal').showModal();
    } catch (err) { toast(err.message, true); }
  });
  document.getElementById('disk-config-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var paths = event.target.elements.paths.value.split(/[\n,]+/).map(function (path) { return path.trim(); }).filter(Boolean);
    try {
      await api('/api/node-disk-config', { method: 'PUT', body: JSON.stringify({ paths: paths }) });
      event.target.closest('dialog').close();
      toast('磁盘监控目录已保存，节点将在下一采集周期更新');
      setTimeout(loadNodes, 35000);
    } catch (err) { toast(err.message, true); }
  });
  var allBuilds = [];
  var allBuildConfigs = [];
  var editingBuildConfigId = null;
  var buildHistoryTimer = null;
  async function loadBuilds() {
    var data = await Promise.all([api('/api/builds?limit=500'), api('/api/build-configs')]);
    allBuilds = data[0];
    allBuildConfigs = data[1];
    renderBuildConfigs();
    renderBuilds();
  }
  function renderBuildConfigs() {
    var rows = pageRows('build-configs', allBuildConfigs, renderBuildConfigs, 8);
    document.getElementById('build-configs-list').innerHTML = rows.length ? rows.map(function (x) {
      var status = x.enabled ? '<span class="build-status is-enabled"><i></i>启用中</span>' : '<span class="build-status is-disabled"><i></i>已停用</span>';
      var toggleLabel = x.enabled ? '停用' : '启用';
      var toggleClass = x.enabled ? 'action-warn' : 'action-success';
      return '<div class="artifact-card build-config-card"><div class="build-config-main"><div class="build-config-heading"><div><strong>' + esc(x.name) + '</strong><small>' + esc(x.description || x.git_url) + '</small></div><div class="build-config-status">' + status + '</div></div><div class="config-meta"><span>Git：' + esc(x.git_ref) + '</span><span>镜像：' + esc(x.destination) + '</span><span>凭证：Git ' + (x.has_git_credentials ? '已配置' : '未配置') + ' · Harbor ' + (x.has_registry_credentials ? '已配置' : '未配置') + '</span><span>超时：' + x.timeout_seconds + ' 秒</span><span>保留：' + x.retention_count + ' 条</span></div></div><div class="build-config-actions"><button class="action-success" onclick="kpRunBuildConfig(' + x.id + ')">运行</button><button class="action-edit" onclick="kpEditBuildConfig(' + x.id + ')">编辑</button><button class="' + toggleClass + '" onclick="kpToggleBuildConfig(' + x.id + ')">' + toggleLabel + '</button><button class="action-danger" onclick="kpDeleteBuildConfig(' + x.id + ')">删除</button></div></div>';
    }).join('') : '<p class="muted">暂无构建配置，请先新建一个可复用配置。</p>';
  }
  function renderBuilds() {
    var rows = pageRows('builds', allBuilds, renderBuilds, 10);
    document.getElementById('builds-body').innerHTML = rows
      .map(function (x) {
        return (
          '<tr><td>' +
          x.id +
          '</td><td>' +
          esc(x.name) + (x.config_id ? ' <span class="tag">配置 #' + x.config_id + '</span>' : '') +
          '</td><td>' +
          esc(x.git_url) +
          ' @ ' +
          esc(x.git_ref) +
          '</td><td>' +
          esc(x.destination) +
          '</td><td>' +
          statusBadge(x.status) +
          '</td><td>' +
          esc(x.created_by) +
          '</td><td><div class="actions build-history-actions">' +
          (x.config_id ? '<button class="action-edit" onclick="kpEditBuildConfig(' + x.config_id + ')">编辑配置</button>' : '') +
          '<button class="action-info" onclick="kpBuildLog(' +
          x.id +
          ')">日志</button><button class="action-danger" onclick="kpDeleteBuild(' + x.id + ')">删除</button></div></td></tr>'
        );
      })
      .join('') || '<tr><td colspan="7" class="muted">暂无构建任务</td></tr>';
  }
  async function refreshBuildLog(id) {
    var x = await api('/api/builds/' + id);
    document.getElementById('build-log').textContent = x.message || '暂无日志';
    var live = x.status === 'pending' || x.status === 'running';
    document.getElementById('log-live-state').textContent = live ? '● 实时更新中' : '● ' + x.status;
    if (!live && buildHistoryTimer) { clearInterval(buildHistoryTimer); buildHistoryTimer = null; }
    return x;
  }
  window.kpBuildLog = async function (id) {
    try {
      if (buildHistoryTimer) { clearInterval(buildHistoryTimer); buildHistoryTimer = null; }
      document.getElementById('log-modal-title').textContent = '构建日志 #' + id;
      document.getElementById('log-modal').querySelector('.log-modal').classList.remove('log-expanded');
      document.getElementById('log-expand').textContent = '放大';
      await refreshBuildLog(id);
      document.getElementById('log-modal').showModal();
      buildHistoryTimer = setInterval(function () { refreshBuildLog(id).catch(function () {}); }, 2500);
    } catch (e) { toast(e.message, true); }
  };
  window.kpRefreshBuildHistory = function () { loadBuilds(); };
  window.kpDeleteBuild = async function (id) {
    if (!confirm('确认删除这条执行历史？运行中的任务也会被停止。')) return;
    try { await api('/api/builds/' + id, { method: 'DELETE' }); toast('执行历史已删除'); loadBuilds(); }
    catch (err) { toast(err.message, true); }
  };
  window.kpToggleBuildConfig = async function (id) {
    var item = allBuildConfigs.find(function (x) { return x.id === id; });
    if (!item) return;
    var action = item.enabled ? '停用' : '启用';
    if (!confirm('确认' + action + '构建配置“' + item.name + '”？')) return;
    try {
      await api('/api/build-configs/' + id, { method: 'PUT', body: JSON.stringify({
        name: item.name, description: item.description || '', enabled: !item.enabled,
        git_url: item.git_url, git_ref: item.git_ref, context_dir: item.context_dir,
        dockerfile: item.dockerfile, destination: item.destination, namespace: item.namespace,
        git_secret_name: '', registry_secret_name: '', build_args: item.build_args || {},
        timeout_seconds: item.timeout_seconds, allow_concurrent: item.allow_concurrent,
        retention_count: item.retention_count,
      }) });
      toast('构建配置已' + action);
      loadBuilds();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('log-refresh').onclick = function () {
    var title = document.getElementById('log-modal-title').textContent;
    var match = title.match(/#(\d+)/);
    if (match) refreshBuildLog(Number(match[1])).catch(function (err) { toast(err.message, true); });
  };
  document.getElementById('log-expand').onclick = function () {
    var panel = document.getElementById('log-modal').querySelector('.log-modal');
    var expanded = panel.classList.toggle('log-expanded');
    this.textContent = expanded ? '缩小' : '放大';
  };
  document.getElementById('pod-log-refresh').onclick = function () {
    refreshPodLog(true, true).catch(function () {});
  };
  document.getElementById('pod-log-clear').onclick = function () {
    if (!podLogState) return;
    podLogState.clearBaseline = podLogState.logs || '';
    podLogState.clearAnchor = podLogState.clearBaseline.trimEnd().split(/\r?\n/).pop() || '';
    renderPodLogText(false);
    document.getElementById('pod-log-updated-at').textContent = '已清屏，等待新日志';
    toast('显示区域已清空，实时刷新只显示之后产生的新日志');
  };
  document.getElementById('pod-log-container').onchange = function () {
    document.getElementById('pod-log-previous').checked = false;
    reloadPodLog();
  };
  ['pod-log-lines', 'pod-log-since', 'pod-log-timestamps', 'pod-log-previous'].forEach(function (id) {
    document.getElementById(id).onchange = function () {
      reloadPodLog();
    };
  });
  document.getElementById('pod-log-follow').onchange = function () {
    document.getElementById('pod-log-live-state').textContent = this.checked ? '● 实时更新中' : '● 已暂停';
    if (this.checked) {
      refreshPodLog(true).catch(function () {});
      startPodLogPolling();
    } else {
      stopPodLogPolling();
    }
  };
  document.getElementById('pod-log-filter').oninput = function () { renderPodLogText(false); };
  document.getElementById('pod-log-autoscroll').onchange = function () {
    if (this.checked) renderPodLogText(true);
  };
  document.getElementById('pod-log-wrap').onclick = function () {
    var content = document.getElementById('pod-log-content');
    var wrapped = content.classList.toggle('is-wrapped');
    this.classList.toggle('is-active', wrapped);
    this.setAttribute('aria-pressed', String(wrapped));
    this.textContent = wrapped ? '取消换行' : '自动换行';
  };
  document.getElementById('pod-log-download').onclick = function () {
    var text = podLogVisibleText();
    if (!text) return toast('当前没有可下载的日志', true);
    var container = document.getElementById('pod-log-container').value || 'container';
    var stamp = new Date().toISOString().replace(/:/g, '-').slice(0, 19);
    var link = document.createElement('a');
    var url = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
    link.href = url;
    link.download = podLogState.name + '_' + container + '_' + stamp + '.log';
    document.body.appendChild(link);
    link.click();
    link.remove();
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    toast('日志文件已开始下载');
  };
  document.getElementById('pod-log-expand').onclick = function () {
    var modal = document.getElementById('pod-log-modal');
    var expanded = modal.classList.toggle('is-log-expanded');
    this.textContent = expanded ? '还原日志区' : '放大日志区';
    requestAnimationFrame(function () {
      document.getElementById('pod-log-content').scrollTop = document.getElementById('pod-log-content').scrollHeight;
    });
  };
  document.getElementById('log-modal').addEventListener('close', function () {
    if (buildHistoryTimer) { clearInterval(buildHistoryTimer); buildHistoryTimer = null; }
  });
  document.getElementById('pod-log-modal').addEventListener('close', function () {
    stopPodLogPolling();
    podLogRequestId += 1;
    podLogState = null;
    this.classList.remove('is-log-expanded');
  });
  function prepareBuildConfigModal(item) {
    editingBuildConfigId = item ? item.id : null;
    var form = document.getElementById('build-config-form');
    form.reset();
    form.elements.name.value = item ? item.name : '';
    form.elements.description.value = item ? item.description : '';
    form.elements.git_url.value = item ? item.git_url : '';
    form.elements.destination.value = item ? item.destination : '';
    form.elements.enabled.checked = item ? item.enabled : true;
    form.elements.allow_concurrent.checked = item ? item.allow_concurrent : false;
    form.elements.namespace.value = item ? item.namespace : 'devops-platform';
    form.elements.git_ref.value = item ? item.git_ref : 'main';
    form.elements.context_dir.value = item ? item.context_dir : '.';
    form.elements.dockerfile.value = item ? item.dockerfile : 'Dockerfile';
    form.elements.timeout_seconds.value = item ? item.timeout_seconds : 1800;
    form.elements.retention_count.value = item ? item.retention_count : 20;
    form.elements.build_args.value = JSON.stringify(item ? (item.build_args || {}) : {}, null, 2);
    form.elements.git_username.value = '';
    form.elements.git_password.value = '';
    form.elements.registry_username.value = '';
    form.elements.registry_password.value = '';
    form.elements.clear_git_credentials.checked = false;
    form.elements.clear_registry_credentials.checked = false;
    document.getElementById('clear-git-credentials-field').classList.toggle('hidden', !(item && item.has_git_credentials));
    document.getElementById('clear-registry-credentials-field').classList.toggle('hidden', !(item && item.has_registry_credentials));
    document.getElementById('build-config-modal-title').textContent = item ? '编辑构建配置' : '新建构建配置';
  }
  window.kpEditBuildConfig = function (id) {
    var item = allBuildConfigs.find(function (x) { return x.id === id; });
    if (!item) return;
    var modal = document.getElementById('build-config-modal');
    prepareBuildConfigModal(item);
    modal.showModal();
  };
  window.kpRunBuildConfig = function (id) {
    var item = allBuildConfigs.find(function (x) { return x.id === id; });
    if (!item) return;
    var form = document.getElementById('build-run-form');
    form.reset();
    form.elements.config_id.value = id;
    form.elements.build_args.value = JSON.stringify(item.build_args || {}, null, 2);
    document.getElementById('build-run-summary').textContent = item.name + ' · ' + item.git_url + ' · 默认分支 ' + item.git_ref;
    document.getElementById('build-run-modal').showModal();
  };
  window.kpDeleteBuildConfig = async function (id) {
    if (!confirm('确认删除该构建配置？历史执行记录会保留。')) return;
    try { await api('/api/build-configs/' + id, { method: 'DELETE' }); toast('构建配置已删除'); loadBuilds(); } catch (err) { toast(err.message, true); }
  };
  document.getElementById('build-config-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var f = new FormData(e.target);
    var payload;
    try {
      payload = { name: f.get('name'), description: f.get('description'), enabled: f.get('enabled') === 'on', git_url: f.get('git_url'), git_ref: f.get('git_ref'), context_dir: f.get('context_dir'), dockerfile: f.get('dockerfile'), destination: f.get('destination'), namespace: f.get('namespace'), git_username: f.get('git_username'), git_password: f.get('git_password'), registry_username: f.get('registry_username'), registry_password: f.get('registry_password'), clear_git_credentials: f.get('clear_git_credentials') === 'on', clear_registry_credentials: f.get('clear_registry_credentials') === 'on', build_args: JSON.parse(f.get('build_args') || '{}'), timeout_seconds: Number(f.get('timeout_seconds')), allow_concurrent: f.get('allow_concurrent') === 'on', retention_count: Number(f.get('retention_count')) };
    } catch (err) { toast('构建参数必须是合法 JSON', true); return; }
    try { await api('/api/build-configs' + (editingBuildConfigId ? '/' + editingBuildConfigId : ''), { method: editingBuildConfigId ? 'PUT' : 'POST', body: JSON.stringify(payload) }); e.target.closest('dialog').close(); toast(editingBuildConfigId ? '构建配置已更新' : '构建配置已保存'); loadBuilds(); } catch (err) { toast(err.message, true); }
  });
  document.getElementById('build-run-form').addEventListener('submit', async function (e) {
    e.preventDefault();
    var f = new FormData(e.target); var args;
    try { args = JSON.parse(f.get('build_args') || '{}'); } catch (err) { toast('构建参数必须是合法 JSON', true); return; }
    try { await api('/api/build-configs/' + f.get('config_id') + '/run', { method: 'POST', body: JSON.stringify({ git_ref: f.get('git_ref') || null, destination: f.get('destination') || null, build_args: args }) }); e.target.closest('dialog').close(); toast('构建任务已创建'); loadBuilds(); } catch (err) { toast(err.message, true); }
  });
  document
    .getElementById('build-form')
    .addEventListener('submit', async function (e) {
      e.preventDefault();
      var f = new FormData(e.target);
      var p = {
        name: f.get('name'),
        git_url: f.get('git_url'),
        git_ref: f.get('git_ref'),
        context_dir: f.get('context_dir'),
        dockerfile: f.get('dockerfile'),
        destination: f.get('destination'),
        namespace: f.get('namespace'),
        registry_secret_name: f.get('registry_secret_name'),
        git_secret_name: '',
        build_args: {},
      };
      try {
        await api('/api/builds', { method: 'POST', body: JSON.stringify(p) });
        e.target.closest('dialog').close();
        toast('构建任务已创建');
        loadBuilds();
      } catch (err) {
        toast(err.message, true);
      }
    });
  var resourceNamespaces = [];
  var resourceTypes = [];
  var allResourceRows = [];
  var currentResource = null;
  function selectedResourceType() {
    var value = document.getElementById('resource-type').value.split('|');
    return { api_version: value[0] || '', kind: value[1] || '' };
  }
  function resourceQuery(extra) {
    var type = selectedResourceType();
    var params = new URLSearchParams(Object.assign({
      namespace: document.getElementById('resource-namespace').value,
      api_version: type.api_version,
      kind: type.kind,
    }, extra || {}));
    return params.toString();
  }
  async function loadResourceManager() {
    if (!resourceNamespaces.length || !resourceTypes.length) {
      var data = await Promise.all([api('/api/namespaces'), api('/api/k8s/resource-types')]);
      resourceNamespaces = data[0];
      resourceTypes = data[1];
      replaceOptions(document.getElementById('resource-namespace'), resourceNamespaces.map(function (namespace) {
        return { value: namespace, label: namespace };
      }));
      var preferredNamespace = resourceNamespaces.indexOf('devops-platform') >= 0 ? 'devops-platform' : resourceNamespaces[0];
      document.getElementById('resource-namespace').value = preferredNamespace || '';
      replaceOptions(document.getElementById('resource-type'), resourceTypes.map(function (item) {
        return {
          value: item.api_version + '|' + item.kind,
          label: item.kind + (item.editable ? '' : '（只读）'),
        };
      }));
      var deploymentType = resourceTypes.find(function (item) { return item.kind === 'Deployment' && item.api_version === 'apps/v1'; });
      if (deploymentType) document.getElementById('resource-type').value = deploymentType.api_version + '|' + deploymentType.kind;
    }
    await loadResourceRows();
  }
  async function loadResourceRows() {
    var namespace = document.getElementById('resource-namespace').value;
    var type = selectedResourceType();
    if (!namespace || !type.kind) {
      allResourceRows = [];
      renderResourceRows();
      return;
    }
    allResourceRows = await api('/api/k8s/resources?' + resourceQuery());
    if (pagerStates['k8s-resources']) pagerStates['k8s-resources'].page = 1;
    renderResourceRows();
  }
  function renderResourceRows() {
    var rows = pageRows('k8s-resources', allResourceRows, renderResourceRows, 10);
    document.getElementById('resources-body').innerHTML = rows.length
      ? rows.map(function (item) {
          var labels = Object.entries(item.labels || {}).slice(0, 3).map(function (entry) {
            return '<span class="tag">' + esc(entry[0]) + '=' + esc(entry[1]) + '</span>';
          }).join('');
          var podLogButton = item.kind === 'Pod'
            ? '<button class="action-info" onclick="kpPodLog(\'' + esc(item.namespace) + '\',\'' + esc(item.name) + '\')">日志</button>'
            : '';
          var podRestartButton = item.kind === 'Pod'
            ? '<button class="action-warn" onclick="kpRestartPod(\'' + esc(item.namespace) + '\',\'' + esc(item.name) + '\')">重启</button>'
            : '';
          return '<tr><td>' + esc(item.kind) + '</td><td><strong>' + esc(item.name) +
            '</strong></td><td>' + esc(item.summary || '—') + '</td><td><div class="tags">' +
            (labels || '<span class="muted">无</span>') + '</div></td><td>' + esc(formatDate(item.created_at)) +
            '</td><td><div class="actions">' + podLogButton + podRestartButton + '<button class="action-edit" onclick="kpOpenResource(\'' + esc(item.name) + '\')">' +
            (currentIsAdmin ? '查看 / 编辑' : '查看') + '</button></div></td></tr>';
        }).join('')
      : '<tr><td colspan="6" class="muted">当前类型暂无资源</td></tr>';
  }
  document.getElementById('resource-namespace').addEventListener('change', function () {
    loadResourceRows().catch(function (err) { toast(err.message, true); });
  });
  document.getElementById('resource-type').addEventListener('change', function () {
    loadResourceRows().catch(function (err) { toast(err.message, true); });
  });
  function renderResourceDetail(data) {
    var editor = document.getElementById('resource-editor-content');
    editor.value = data.content;
    editor.readOnly = !currentIsAdmin;
    document.getElementById('resource-save-button').classList.toggle('hidden', !currentIsAdmin);
    document.getElementById('resource-rollback-button').classList.toggle('hidden', !currentIsAdmin);
    document.getElementById('resource-save-button').disabled = !data.editable;
    document.getElementById('resource-diff-button').disabled = !data.can_compare;
    document.getElementById('resource-rollback-button').disabled = !data.can_rollback;
    var history = data.history || [];
    var historyItems = history.length
      ? history.map(function (item) {
          var action = { baseline: '初始快照', update: '保存', rollback: '回退' }[item.action] || item.action;
          return '<span title="v' + item.version + ' · ' + action + ' · ' + esc(item.created_by) + ' · ' + esc(formatDate(item.created_at)) + '">v' + item.version + ' · ' + action + ' · ' + esc(item.created_by) + ' · ' + esc(formatDate(item.created_at)) + '</span>';
        }).join('')
      : '<span class="muted">尚无版本记录，首次保存后自动建立。</span>';
    document.getElementById('resource-history').innerHTML =
      '<div class="resource-history-heading"><strong>配置版本</strong><span>' + history.length + ' 个版本' +
      '</span></div><div class="resource-history-list">' + historyItems + '</div>';
  }
  window.kpOpenResource = async function (name) {
    try {
      var type = selectedResourceType();
      currentResource = {
        namespace: document.getElementById('resource-namespace').value,
        api_version: type.api_version, kind: type.kind, name: name,
      };
      var data = await api('/api/k8s/resource?' + resourceQuery({ name: name }));
      document.getElementById('resource-editor-title').textContent = type.kind + ' / ' + name;
      document.getElementById('resource-editor-meta').textContent = currentResource.namespace + ' · ' + type.api_version +
        (currentIsAdmin ? '' : ' · 只读');
      renderResourceDetail(data);
      document.getElementById('resource-editor-modal').showModal();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('resource-save-button').addEventListener('click', async function () {
    if (!currentResource || !confirm('确认保存该资源配置？保存前后会自动记录版本。')) return;
    try {
      var data = await api('/api/k8s/resource?' + new URLSearchParams(currentResource).toString(), {
        method: 'PUT', body: JSON.stringify({ content: document.getElementById('resource-editor-content').value }),
      });
      renderResourceDetail(data);
      toast('资源配置已保存并记录版本');
      loadResourceRows();
    } catch (err) { toast(err.message, true); }
  });
  document.getElementById('resource-diff-button').addEventListener('click', async function () {
    if (!currentResource) return;
    try {
      var data = await api('/api/k8s/resource/diff?' + new URLSearchParams(currentResource).toString(), {
        method: 'POST',
        body: JSON.stringify({ content: document.getElementById('resource-editor-content').value }),
      });
      document.getElementById('resource-diff-title').textContent = '当前版本与 v' + data.previous_version + ' 的差异';
      var rows = data.rows || [];
      document.getElementById('resource-diff-content').innerHTML =
        '<div class="diff-header"><div>' + esc(data.previous_label || ('历史版本 v' + data.previous_version)) +
        '</div><div>' + esc(data.current_label || '当前编辑内容') + '</div></div>' +
        (rows.length ? rows.map(function (row) {
          function cell(side) {
            var number = row[side + '_number'];
            var content = row[side + '_text'];
            return '<div class="diff-cell ' + side + (number == null ? ' empty' : '') + '">' +
              '<span class="diff-line-number">' + (number == null ? '' : number) + '</span>' +
              '<code class="diff-line-text">' + esc(content || ' ') + '</code></div>';
          }
          return '<div class="diff-row ' + esc(row.type) + '">' + cell('left') + cell('right') + '</div>';
        }).join('') : '<div class="diff-empty">两个版本内容一致</div>');
      document.getElementById('resource-diff-modal').showModal();
    } catch (err) { toast(err.message, true); }
  });
  document.getElementById('resource-rollback-button').addEventListener('click', async function () {
    if (!currentResource || !confirm('确认回退到上一个版本？当前版本仍会保留在历史记录中。')) return;
    try {
      var data = await api('/api/k8s/resource/rollback?' + new URLSearchParams(currentResource).toString(), {
        method: 'POST', body: JSON.stringify({ version: null }),
      });
      renderResourceDetail(data);
      toast('资源已回退到上一个版本');
      loadResourceRows();
    } catch (err) { toast(err.message, true); }
  });
  document.getElementById('resource-expand-button').addEventListener('click', function () {
    var modal = document.getElementById('resource-editor-modal');
    var expanded = modal.classList.toggle('expanded');
    this.textContent = expanded ? '缩小' : '放大';
  });
  function eventItem(x, allowDelete) {
    return (
      '<div class="list-item"><div><strong>' +
      esc(x.title) +
      '</strong><small>' +
      esc(x.target) +
      ' · ' +
      esc(x.message).replace(/\n/g, ' · ') +
      ' · ' +
      esc(formatDate(x.started_at)) +
      '</small></div><div class="actions">' +
      severityBadge(x.severity) +
      incidentStatus(x.status) +
      (allowDelete ? '<button class="action-danger" onclick="kpDeleteAlertEvent(' + x.id + ')">删除</button>' : '') +
      '</div>' +
      '</div>'
    );
  }
  var metricLabels = {
    pod_cpu_percent: 'Pod CPU %',
    pod_memory_percent: 'Pod 内存 %',
    node_cpu_percent: '节点 CPU %',
    node_memory_percent: '节点内存 %',
    node_disk_percent: '节点磁盘 %',
    pod_restarts: 'Pod 重启次数',
    pod_not_running: 'Pod 非 Running',
  };
  ['rule-form', 'rule-template-form'].forEach(function (formId) {
    var metricSelect = document.getElementById(formId).elements.metric;
    if (metricSelect && !Array.from(metricSelect.options).some(function (option) { return option.value === 'node_disk_percent'; })) {
      metricSelect.add(new Option('节点磁盘 %', 'node_disk_percent'));
    }
  });
  var channelTypeLabels = {
    email: '邮件',
    feishu: '飞书',
    dingtalk: '钉钉',
    wecom: '企业微信',
    phone_webhook: '电话网关',
  };
  var allAlertRules = [];
  var allAlertEvents = [];
  var alertChannels = [];
  var editingRuleId = null;
  async function loadAlerts() {
    var data = await Promise.all([
      api('/api/alert-rules'),
      api('/api/channels'),
      api('/api/alert-rule-templates'),
    ]);
    allAlertRules = data[0];
    alertChannels = data[1];
    allRuleTemplates = data[2];
    renderAlertRules();
  }
  function renderAlertRules() {
    var rows = pageRows('rules', allAlertRules, renderAlertRules, 10);
    var channelNames = Object.fromEntries(
      alertChannels.map(function (channel) {
        return [channel.id, channel.name];
      }),
    );
    document.getElementById('rules-list').innerHTML = rows.length
      ? rows
          .map(function (x) {
            var linkedChannels = (x.channel_ids || [])
              .map(function (id) {
                return channelNames[id] || '已删除渠道 #' + id;
              })
              .join('、');
            return (
              '<div class="list-item"><div><strong>' +
              esc(x.name) +
              '</strong><small>' +
              esc(metricLabels[x.metric] || x.metric) +
              ' ' +
              esc(x.operator) +
              ' ' +
              x.threshold +
              ' · ' +
              x.duration_seconds +
              '秒 · ' +
              (x.repeat_interval_minutes === 0 ? '仅通知一次' : '每 ' + x.repeat_interval_minutes + ' 分钟重复') +
              ' · ' +
              esc(severityLabels[x.severity] || x.severity) +
              ' · 模板：' + esc(x.template_name || '未关联') +
              ' · 渠道：' +
              esc(linkedChannels || '未关联') +
              '</small></div><div class="actions"><button class="action-edit" onclick="kpEditRule(' +
              x.id +
              ')">编辑</button><button class="action-danger" onclick="kpDeleteRule(' +
              x.id +
              ')">删除</button></div></div>'
            );
          })
          .join('')
      : '<p class="muted">暂无规则</p>';
  }
  function renderAlertEvents() {
    var filter = document.getElementById('alert-event-filter').value;
    var filtered = allAlertEvents.filter(function (item) {
      return filter === 'all' || item.status === filter;
    });
    var rows = pageRows('events', filtered, renderAlertEvents, 10);
    document.getElementById('events-list').innerHTML = rows.length
      ? rows.map(function (item) { return eventItem(item, true); }).join('')
      : '<p class="muted empty-state">当前筛选条件下暂无告警事件</p>';
  }
  async function loadAlertEvents() {
    allAlertEvents = await api('/api/alerts?limit=1000');
    renderAlertEvents();
  }
  document.getElementById('alert-event-filter').addEventListener('change', function () {
    if (pagerStates.events) pagerStates.events.page = 1;
    renderAlertEvents();
  });
  window.kpDeleteAlertEvent = async function (id) {
    if (!confirm('确认删除该告警事件？此操作不会删除对应的告警规则。')) return;
    try {
      await api('/api/alerts/' + id, { method: 'DELETE' });
      toast('告警事件已删除');
      await loadAlertEvents();
    } catch (err) { toast(err.message, true); }
  };
  var ruleForm = document.getElementById('rule-form');
  async function prepareRuleModal() {
    editingRuleId = null;
    ruleForm.reset();
    document.getElementById('rule-modal-title').textContent = '新建告警规则';
    var data = await Promise.all([api('/api/channels'), api('/api/alert-rule-templates')]);
    alertChannels = data[0];
    allRuleTemplates = data[1];
    replaceOptions(ruleForm.elements.template_id, [{ value: '', label: '请选择规则模板' }].concat(
      allRuleTemplates.map(function (item) { return { value: item.id, label: item.name + ' · ' + (metricLabels[item.metric] || item.metric) }; }),
    ));
    document.getElementById('rule-channel-options').innerHTML = alertChannels.length
      ? alertChannels
          .map(function (channel) {
            return (
              '<label class="check-option"><input type="checkbox" name="channel_ids" value="' +
              channel.id +
              '" ' +
              (channel.enabled ? '' : 'disabled') +
              '><span>' +
              esc(channel.name) +
              ' · ' +
              esc(channelTypeLabels[channel.channel_type] || channel.channel_type) +
              (channel.enabled ? '' : '（已停用）') +
              '</span></label>'
            );
          })
          .join('')
      : '<p class="muted">暂无通知渠道，请先在“通知渠道”中创建。</p>';
    updateRuleMetricFields(true);
  }
  window.kpEditRule = async function (id) {
    var rule = allAlertRules.find(function (item) {
      return item.id === id;
    });
    if (!rule) return;
    editingRuleId = id;
    ruleForm.reset();
    document.getElementById('rule-modal-title').textContent = '编辑告警规则';
    document.getElementById('rule-modal').showModal();
    try {
      var data = await Promise.all([api('/api/channels'), api('/api/alert-rule-templates')]);
      alertChannels = data[0];
      allRuleTemplates = data[1];
      replaceOptions(ruleForm.elements.template_id, [{ value: '', label: '请选择规则模板' }].concat(
        allRuleTemplates.map(function (item) { return { value: item.id, label: item.name + ' · ' + (metricLabels[item.metric] || item.metric) }; }),
      ));
      document.getElementById('rule-channel-options').innerHTML = alertChannels.length
        ? alertChannels.map(function (channel) {
            return '<label class="check-option"><input type="checkbox" name="channel_ids" value="' +
              channel.id + '" ' + ((rule.channel_ids || []).indexOf(channel.id) >= 0 ? 'checked ' : '') +
              (channel.enabled || (rule.channel_ids || []).indexOf(channel.id) >= 0 ? '' : 'disabled') + '><span>' + esc(channel.name) + ' · ' +
              esc(channelTypeLabels[channel.channel_type] || channel.channel_type) +
              (channel.enabled ? '' : '（已停用）') + '</span></label>';
          }).join('')
        : '<p class="muted">暂无通知渠道，请先创建通知渠道。</p>';
      ruleForm.elements.name.value = rule.name;
      ruleForm.elements.template_id.value = rule.template_id || '';
      ruleForm.elements.metric.value = rule.metric;
      ruleForm.elements.operator.value = rule.operator;
      ruleForm.elements.threshold.value = rule.threshold;
      ruleForm.elements.duration_seconds.value = rule.duration_seconds;
      ruleForm.elements.repeat_interval_minutes.value = rule.repeat_interval_minutes;
      ruleForm.elements.severity.value = rule.severity;
      ruleForm.elements.namespace.value = rule.namespace;
      ruleForm.elements.target_pattern.value = rule.target_pattern;
      updateRuleMetricFields(false);
    } catch (err) {
      document.getElementById('rule-modal').close();
      toast(err.message, true);
    }
  };
  function updateRuleMetricFields(resetValue) {
    var metric = ruleForm.elements.metric.value;
    var operatorField = document.getElementById('rule-operator-field');
    var thresholdField = document.getElementById('rule-threshold-field');
    var threshold = ruleForm.elements.threshold;
    var operator = ruleForm.elements.operator;
    var durationLabel = document.getElementById('rule-duration-label');
    var hint = document.getElementById('rule-metric-hint');
    var namespaceField = document.getElementById('rule-namespace-field');
    operatorField.classList.remove('hidden');
    thresholdField.classList.remove('hidden');
    operator.disabled = false;
    threshold.disabled = false;
    durationLabel.textContent = '持续秒数';
    hint.textContent = '';
    namespaceField.classList.toggle('hidden', metric === 'node_cpu_percent' || metric === 'node_memory_percent' || metric === 'node_disk_percent');
    if (metric === 'pod_restarts') {
      operator.value = '>=';
      operator.disabled = true;
      threshold.step = '1';
      threshold.min = '1';
      if (resetValue) threshold.value = '1';
      hint.textContent = '当 Pod 累计重启次数达到或超过该整数，并持续指定时间后告警。';
    } else if (metric === 'pod_not_running') {
      operator.value = '==';
      threshold.value = '1';
      operator.disabled = true;
      threshold.disabled = true;
      operatorField.classList.add('hidden');
      thresholdField.classList.add('hidden');
      durationLabel.textContent = '非 Running 持续秒数';
      hint.textContent = '无需设置数值阈值；工作负载 Pod 连续处于非 Running 状态达到指定时间后告警，已成功完成的 Job Pod 自动忽略。';
    } else if (metric === 'node_cpu_percent' || metric === 'node_memory_percent' || metric === 'node_disk_percent') {
      ruleForm.elements.namespace.value = '_cluster';
      hint.textContent = metric === 'node_disk_percent'
        ? '节点磁盘按已配置的挂载目录采集；目标匹配填写 节点名:/挂载目录 或 *。'
        : '节点是集群级资源，不属于 Kubernetes 命名空间；目标匹配填写节点名或 *。';
    } else {
      if (ruleForm.elements.namespace.value === '_cluster') ruleForm.elements.namespace.value = '*';
      threshold.step = '0.01';
      threshold.min = '0';
      if (resetValue) {
        operator.value = '>';
        threshold.value = '80';
      }
    }
  }
  ruleForm.elements.metric.addEventListener('change', function () {
    var selectedTemplate = allRuleTemplates.find(function (item) {
      return item.id === Number(ruleForm.elements.template_id.value);
    });
    if (selectedTemplate && selectedTemplate.metric !== ruleForm.elements.metric.value) {
      ruleForm.elements.template_id.value = '';
      toast('指标已变更，请重新选择匹配的规则模板', true);
    }
    updateRuleMetricFields(true);
  });
  function applySelectedRuleTemplate() {
    var id = Number(ruleForm.elements.template_id.value);
    var item = allRuleTemplates.find(function (row) { return row.id === id; });
    if (!item) return;
    ruleForm.elements.metric.value = item.metric;
    ruleForm.elements.operator.value = item.operator;
    ruleForm.elements.threshold.value = item.threshold;
    ruleForm.elements.duration_seconds.value = item.duration_seconds;
    ruleForm.elements.repeat_interval_minutes.value = item.repeat_interval_minutes;
    ruleForm.elements.severity.value = item.severity;
    ruleForm.elements.namespace.value = item.namespace;
    ruleForm.elements.target_pattern.value = item.target_pattern;
    updateRuleMetricFields(false);
  }
  ruleForm.elements.template_id.addEventListener('change', applySelectedRuleTemplate);
  window.kpDeleteRule = async function (id) {
    if (!confirm('确认删除规则？')) return;
    try {
      await api('/api/alert-rules/' + id, { method: 'DELETE' });
      toast('告警规则已删除，历史事件已保留');
      await loadAlerts();
    } catch (e) {
      toast(e.message, true);
    }
  };
  ruleForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      var f = new FormData(e.target);
      var metric = f.get('metric');
      var ids = f.getAll('channel_ids').map(Number);
      var p = {
        name: f.get('name'),
        metric: metric,
        operator: metric === 'pod_not_running' ? '==' : metric === 'pod_restarts' ? '>=' : f.get('operator'),
        threshold: metric === 'pod_not_running' ? 1 : Number(f.get('threshold')),
        duration_seconds: Number(f.get('duration_seconds')),
        repeat_interval_minutes: Number(f.get('repeat_interval_minutes')),
        severity: f.get('severity'),
        namespace: metric === 'node_cpu_percent' || metric === 'node_memory_percent' || metric === 'node_disk_percent' ? '_cluster' : f.get('namespace'),
        target_pattern: f.get('target_pattern'),
        template_id: Number(f.get('template_id')),
        channel_ids: ids,
        enabled: true,
      };
      try {
        await api(editingRuleId ? '/api/alert-rules/' + editingRuleId : '/api/alert-rules', {
          method: editingRuleId ? 'PUT' : 'POST',
          body: JSON.stringify(p),
        });
        e.target.closest('dialog').close();
        toast(editingRuleId ? '告警规则已更新' : '告警规则已保存');
        editingRuleId = null;
        loadAlerts();
      } catch (err) {
        toast(err.message, true);
      }
    });
  var allRuleTemplates = [];
  async function loadRuleTemplates() {
    allRuleTemplates = await api('/api/alert-rule-templates');
    renderRuleTemplates();
  }
  function renderRuleTemplates() {
    var rows = pageRows('rule-templates', allRuleTemplates, renderRuleTemplates, 10);
    document.getElementById('rule-templates-list').innerHTML = rows.length
      ? rows.map(function (item) {
          return '<div class="artifact-card rule-template-card"><div class="rule-template-info"><div class="rule-template-heading"><strong>' +
            esc(item.name) + '</strong><span class="rule-template-severity ' +
            (item.severity === 'critical' ? 'critical' : item.severity === 'warning' ? 'warning' : 'info') + '">' +
            esc(severityLabels[item.severity] || item.severity) + '</span></div><p>' +
            esc(item.description || '通用告警模板') + '</p><div class="tags"><span class="tag">' +
            esc(metricLabels[item.metric] || item.metric) + '</span><span class="tag">' +
            esc(item.operator) + ' ' + item.threshold + '</span><span class="tag">持续 ' +
            item.duration_seconds + ' 秒</span><span class="tag">' +
            (item.repeat_interval_minutes === 0 ? '仅通知一次' : '每 ' + item.repeat_interval_minutes + ' 分钟重复') +
            '</span></div></div><div class="actions rule-template-actions"><button class="action-success" onclick="kpApplyRuleTemplate(' +
            item.id + ')">套用</button><button class="action-danger" onclick="kpDeleteRuleTemplate(' +
            item.id + ')">删除</button></div></div>';
        }).join('')
      : '<p class="muted">暂无规则模板</p>';
  }
  window.kpApplyRuleTemplate = async function (id) {
    var item = allRuleTemplates.find(function (row) { return row.id === id; });
    if (!item) return;
    try {
      await prepareRuleModal();
      ruleForm.elements.template_id.value = String(item.id);
      ruleForm.elements.name.value = item.name + '-规则';
      ruleForm.elements.metric.value = item.metric;
      ruleForm.elements.operator.value = item.operator;
      ruleForm.elements.threshold.value = item.threshold;
      ruleForm.elements.duration_seconds.value = item.duration_seconds;
      ruleForm.elements.repeat_interval_minutes.value = item.repeat_interval_minutes;
      ruleForm.elements.severity.value = item.severity;
      ruleForm.elements.namespace.value = item.namespace;
      ruleForm.elements.target_pattern.value = item.target_pattern;
      updateRuleMetricFields(false);
      document.getElementById('rule-modal').showModal();
    } catch (err) {
      toast(err.message, true);
    }
  };
  var ruleTemplatePresets = {
    pod_cpu: { name: 'Pod CPU 高负载', description: 'Pod CPU 使用率持续过高', metric: 'pod_cpu_percent', threshold: 80, duration_seconds: 300, repeat_interval_minutes: 30, severity: 'warning', target_pattern: '*' },
    pod_memory: { name: 'Pod 内存高负载', description: 'Pod 内存使用率持续过高', metric: 'pod_memory_percent', threshold: 85, duration_seconds: 300, repeat_interval_minutes: 30, severity: 'warning', target_pattern: '*' },
    node_cpu: { name: '节点 CPU 高负载', description: '节点 CPU 使用率持续过高', metric: 'node_cpu_percent', threshold: 80, duration_seconds: 300, repeat_interval_minutes: 30, severity: 'warning', target_pattern: '*' },
    node_memory: { name: '节点内存高负载', description: '节点内存使用率持续过高', metric: 'node_memory_percent', threshold: 85, duration_seconds: 300, repeat_interval_minutes: 30, severity: 'warning', target_pattern: '*' },
    node_disk: { name: '节点磁盘高使用率', description: '节点挂载目录使用率持续过高', metric: 'node_disk_percent', threshold: 85, duration_seconds: 300, repeat_interval_minutes: 30, severity: 'warning', target_pattern: '*:/' },
    pod_restarts: { name: 'Pod 频繁重启', description: 'Pod 累计重启次数达到阈值', metric: 'pod_restarts', threshold: 3, duration_seconds: 60, repeat_interval_minutes: 30, severity: 'critical', target_pattern: '*' },
    pod_not_running: { name: 'Pod 非 Running', description: '持续处于非 Running 的工作负载 Pod；已成功完成的 Job Pod 自动忽略', metric: 'pod_not_running', threshold: 1, duration_seconds: 120, repeat_interval_minutes: 30, severity: 'critical', target_pattern: '*' },
  };
  var ruleTemplateForm = document.getElementById('rule-template-form');
  if (!Array.from(ruleTemplateForm.elements.preset.options).some(function (option) { return option.value === 'node_disk'; })) {
    ruleTemplateForm.elements.preset.add(new Option('节点磁盘高使用率', 'node_disk'));
  }
  function applyRuleTemplatePreset() {
    var item = ruleTemplatePresets[ruleTemplateForm.elements.preset.value];
    if (!item) return;
    Object.keys(item).forEach(function (key) { ruleTemplateForm.elements[key].value = item[key]; });
    document.getElementById('rule-template-preview').textContent =
      '指标：' + (metricLabels[item.metric] || item.metric) + '\n' +
      '条件：' + (item.metric === 'pod_not_running' ? '非 Running' : (item.metric === 'pod_restarts' ? '>=' : '>') + ' ' + item.threshold) + '\n' +
      '持续时间：' + item.duration_seconds + ' 秒\n' +
      '重复通知：' + (item.repeat_interval_minutes === 0 ? '仅通知一次' : '每 ' + item.repeat_interval_minutes + ' 分钟') + '\n' +
      '告警级别：' + (severityLabels[item.severity] || item.severity) + '\n' +
      '目标匹配：' + item.target_pattern +
      (item.metric === 'pod_not_running' ? '\n说明：已成功完成的 Job Pod 自动忽略' : '');
  }
  function prepareRuleTemplateModal() {
    ruleTemplateForm.reset();
    ruleTemplateForm.elements.preset.value = 'pod_cpu';
    applyRuleTemplatePreset();
  }
  ruleTemplateForm.elements.preset.addEventListener('change', applyRuleTemplatePreset);
  window.kpDeleteRuleTemplate = async function (id) {
    if (!confirm('确认删除规则模板？')) return;
    try {
      await api('/api/alert-rule-templates/' + id, { method: 'DELETE' });
      toast('规则模板已删除');
      loadRuleTemplates();
    } catch (err) {
      toast(err.message, true);
    }
  };
  ruleTemplateForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(event.target);
    var metric = data.get('metric');
    var payload = {
      name: data.get('name'),
      description: data.get('description'),
      metric: metric,
      operator: metric === 'pod_not_running' ? '==' : metric === 'pod_restarts' ? '>=' : '>',
      threshold: metric === 'pod_not_running' ? 1 : Number(data.get('threshold')),
      duration_seconds: Number(data.get('duration_seconds')),
      repeat_interval_minutes: Number(data.get('repeat_interval_minutes')),
      severity: data.get('severity'),
      namespace: metric.indexOf('node_') === 0 ? '_cluster' : '*',
      target_pattern: data.get('target_pattern'),
    };
    try {
      await api('/api/alert-rule-templates', { method: 'POST', body: JSON.stringify(payload) });
      event.target.closest('dialog').close();
      toast('规则模板已保存');
      loadRuleTemplates();
    } catch (err) {
      toast(err.message, true);
    }
  });

  var allSubscriptions = [];
  var subscriptionChannels = [];
  var editingSubscriptionId = null;
  async function prepareSubscriptionModal(item) {
    var form = document.getElementById('subscription-form');
    form.reset();
    editingSubscriptionId = item ? item.id : null;
    document.getElementById('subscription-modal-title').textContent = item ? '编辑告警订阅' : '新建告警订阅';
    var data = await Promise.all([api('/api/users'), api('/api/alert-rules'), api('/api/channels')]);
    var users = data[0].filter(function (user) { return user.is_active; });
    replaceOptions(form.elements.user_id, users.map(function (user) {
      return { value: user.id, label: (user.display_name || user.username) + '（' + user.username + '） · ' + user.email };
    }));
    replaceOptions(form.elements.rule_id, [{ value: '', label: '全部告警' }].concat(data[1].map(function (rule) {
      return { value: rule.id, label: rule.name };
    })));
    subscriptionChannels = data[2];
    document.getElementById('subscription-channels').innerHTML = subscriptionChannels.length
      ? subscriptionChannels.map(function (channel) {
          return '<label class="check-option"><input type="checkbox" name="channel_ids" value="' + channel.id + '" ' +
            (channel.enabled || (item && (item.channel_ids || []).includes(channel.id)) ? '' : 'disabled') + '><span>' + esc(channel.name) + ' · ' +
            esc(channelTypeLabels[channel.channel_type] || channel.channel_type) +
            (channel.enabled ? '' : '（已停用）') + '</span></label>';
        }).join('')
      : '<p class="muted">暂无可用通知对象</p>';
    if (item) {
      form.elements.name.value = item.name;
      form.elements.user_id.value = String(item.user_id);
      form.elements.rule_id.value = item.rule_id == null ? '' : String(item.rule_id);
      form.elements.enabled.checked = item.enabled;
      Array.from(form.querySelectorAll('input[name="channel_ids"]')).forEach(function (input) {
        input.checked = (item.channel_ids || []).includes(Number(input.value));
      });
    } else {
      form.elements.enabled.checked = true;
    }
  }
  async function loadSubscriptions() {
    var data = await Promise.all([api('/api/alert-subscriptions'), api('/api/channels')]);
    allSubscriptions = data[0];
    subscriptionChannels = data[1];
    renderSubscriptions();
  }
  function renderSubscriptions() {
    var names = Object.fromEntries(subscriptionChannels.map(function (channel) { return [channel.id, channel.name]; }));
    var rows = pageRows('subscriptions', allSubscriptions, renderSubscriptions, 10);
    document.getElementById('subscriptions-list').innerHTML = rows.length
      ? rows.map(function (item) {
          var channels = (item.channel_ids || []).map(function (id) { return names[id] || '#' + id; }).join('、');
          return '<div class="list-item"><div><strong>' + esc(item.name) + '</strong><small>人员：' +
            esc(item.display_name || item.username) + ' · 规则：' + esc(item.rule_name) + ' · 通知对象：' +
            esc(channels || '未选择') + '</small></div><div class="actions">' +
            (item.enabled ? '<span class="badge ok">启用</span>' : '<span class="badge bad">停用</span>') +
            (currentIsAdmin ? '<button class="action-edit" onclick="kpEditSubscription(' + item.id + ')">编辑</button>' +
              '<button class="action-danger" onclick="kpDeleteSubscription(' + item.id + ')">删除</button>' : '') +
            '</div></div>';
        }).join('')
      : '<p class="muted">暂无告警订阅</p>';
  }
  window.kpEditSubscription = async function (id) {
    var item = allSubscriptions.find(function (row) { return row.id === id; });
    if (!item) return;
    try {
      await prepareSubscriptionModal(item);
      document.getElementById('subscription-modal').showModal();
    } catch (err) { toast(err.message, true); }
  };
  window.kpDeleteSubscription = async function (id) {
    if (!confirm('确认删除告警订阅？')) return;
    try {
      await api('/api/alert-subscriptions/' + id, { method: 'DELETE' });
      toast('告警订阅已删除');
      loadSubscriptions();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('subscription-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(event.target);
    var payload = {
      name: data.get('name'), user_id: Number(data.get('user_id')),
      rule_id: data.get('rule_id') ? Number(data.get('rule_id')) : null,
      channel_ids: data.getAll('channel_ids').map(Number), enabled: data.get('enabled') === 'on',
    };
    try {
      await api(editingSubscriptionId ? '/api/alert-subscriptions/' + editingSubscriptionId : '/api/alert-subscriptions', {
        method: editingSubscriptionId ? 'PUT' : 'POST', body: JSON.stringify(payload),
      });
      event.target.closest('dialog').close();
      toast(editingSubscriptionId ? '告警订阅已更新' : '告警订阅已保存');
      editingSubscriptionId = null;
      loadSubscriptions();
    } catch (err) { toast(err.message, true); }
  });

  var allIncidents = [];
  async function loadIncidents() {
    allIncidents = await api('/api/alerts?limit=1000');
    renderIncidents();
  }
  function incidentStatus(value) {
    var labels = { firing: '待处理', acknowledged: '处理中', resolved: '已解决' };
    var css = value === 'resolved' ? 'ok' : value === 'firing' ? 'bad' : 'warn';
    return '<span class="badge ' + css + '">' + esc(labels[value] || value) + '</span>';
  }
  function renderIncidents() {
    var filter = document.getElementById('incident-filter').value;
    var filtered = allIncidents.filter(function (item) {
      if (filter === 'active') return item.status === 'firing' || item.status === 'acknowledged';
      if (filter !== 'all') return item.status === filter;
      return true;
    });
    var rows = pageRows('incidents', filtered, renderIncidents, 10);
    document.getElementById('incidents-list').innerHTML = rows.length
      ? rows.map(function (item) {
          return '<div class="list-item incident-item"><div class="incident-main"><div class="incident-title-row"><strong>' + esc(item.title) +
            '</strong>' + severityBadge(item.severity) + incidentStatus(item.status) + '</div><small>' +
            esc(item.target) + ' · ' + esc(item.message).replace(/\n/g, ' · ') + '</small><div class="incident-meta">' +
            '<span>触发：' + esc(formatDate(item.started_at)) + '</span><span>负责人：' + esc(item.assignee || '未分配') + '</span>' +
            (item.updated_at ? '<span>更新：' + esc(formatDate(item.updated_at)) + '</span>' : '') + '</div>' +
            (item.handling_note ? '<p class="incident-note">' + esc(item.handling_note) + '</p>' : '') +
            '</div><div class="actions"><button class="action-edit" onclick="kpEditIncident(' + item.id + ')">' +
            (item.status === 'firing' ? '开始处理' : item.status === 'resolved' ? '查看 / 重开' : '更新进度') + '</button></div></div>';
        }).join('')
      : '<p class="muted">当前筛选条件下暂无故障</p>';
  }
  document.getElementById('incident-filter').addEventListener('change', function () {
    if (pagerStates.incidents) pagerStates.incidents.page = 1;
    renderIncidents();
  });
  var incidentActionLabels = {
    acknowledge: '确认处理', update: '更新进度', resolve: '标记解决', reopen: '重新打开', auto_resolve: '指标自动恢复',
  };
  window.kpEditIncident = async function (id) {
    try {
      var item = await api('/api/incidents/' + id);
      var form = document.getElementById('incident-form');
      form.reset();
      form.elements.event_id.value = item.id;
      form.elements.status.value = item.status;
      form.elements.assignee.value = item.assignee || currentUsername;
      form.elements.note.value = item.handling_note || '';
      document.getElementById('incident-modal-title').textContent = item.title;
      document.getElementById('incident-modal-meta').textContent = item.target + ' · 触发于 ' + formatDate(item.started_at);
      document.getElementById('incident-summary').innerHTML =
        '<div><span>当前状态</span>' + incidentStatus(item.status) + '</div>' +
        '<div><span>告警级别</span>' + severityBadge(item.severity) + '</div>' +
        '<div><span>当前值</span><strong>' + esc(item.value == null ? '-' : item.value) + '</strong></div>' +
        '<p>' + esc(item.message).replace(/\n/g, '<br>') + '</p>';
      document.getElementById('incident-history').innerHTML = item.activities.length
        ? item.activities.map(function (activity) {
            return '<div class="incident-history-item"><span class="incident-history-dot"></span><div><div><strong>' +
              esc(incidentActionLabels[activity.action] || activity.action) + '</strong><span>' + esc(formatDate(activity.created_at)) +
              '</span></div><small>' + esc(activity.operator) + ' · ' + esc(activity.from_status || '-') + ' → ' +
              esc(activity.to_status || '-') + (activity.assignee ? ' · 负责人：' + esc(activity.assignee) : '') +
              '</small><p>' + esc(activity.note || '无处理说明') + '</p></div></div>';
          }).join('')
        : '<p class="muted">暂无处理记录</p>';
      document.getElementById('incident-modal').showModal();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('incident-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(event.target);
    var id = Number(data.get('event_id'));
    try {
      await api('/api/incidents/' + id, { method: 'PUT', body: JSON.stringify({
        status: data.get('status'), assignee: data.get('assignee'), note: data.get('note'),
      }) });
      event.target.closest('dialog').close();
      toast('故障处理进度已保存');
      loadIncidents();
    } catch (err) { toast(err.message, true); }
  });
  var channelConfigTemplates = {
    email: {
      host: 'smtp.example.com',
      port: 465,
      username: 'alert@example.com',
      password: '请填写 SMTP 密码或授权码',
      from: 'alert@example.com',
      to: ['ops@example.com'],
      ssl: true,
      starttls: false,
    },
    feishu: {
      url: 'https://open.feishu.cn/open-apis/bot/v2/hook/请填写令牌',
      verify_tls: true,
    },
    dingtalk: {
      url: 'https://oapi.dingtalk.com/robot/send?access_token=请填写令牌',
      verify_tls: true,
    },
    wecom: {
      url: 'https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=请填写密钥',
      verify_tls: true,
    },
    phone_webhook: {
      url: 'https://voice-gateway.example.com/api/call',
      phones: ['13800000000'],
      template_code: 'K8S_ALERT',
      headers: { Authorization: 'Bearer 请填写令牌' },
      extra_payload: {},
      verify_tls: true,
    },
  };
  var channelForm = document.getElementById('channel-form');
  function applyChannelTemplate() {
    var type = channelForm.elements.channel_type.value;
    channelForm.elements.config.value = JSON.stringify(
      channelConfigTemplates[type],
      null,
      2,
    );
  }
  var allNotificationTemplates = [];
  async function loadChannelTemplateOptions(selectedId) {
    allNotificationTemplates = await api('/api/notification-templates');
    var type = channelForm.elements.channel_type.value;
    var options = [{ value: '', label: '请选择同类型通知模板' }].concat(
      allNotificationTemplates.filter(function (item) { return item.channel_type === type; }).map(function (item) {
        return { value: item.id, label: item.name };
      }),
    );
    replaceOptions(channelForm.elements.notification_template_id, options);
    channelForm.elements.notification_template_id.value = selectedId ? String(selectedId) : '';
  }
  async function prepareChannelModal() {
    editingChannelId = null;
    channelForm.reset();
    document.getElementById('channel-modal-title').textContent = '新建通知渠道';
    channelForm.elements.channel_type.value = 'email';
    applyChannelTemplate();
    await loadChannelTemplateOptions(null);
  }
  channelForm.elements.channel_type.addEventListener('change', async function () {
    applyChannelTemplate();
    try {
      await loadChannelTemplateOptions(null);
    } catch (err) { toast(err.message, true); }
  });
  var allChannels = [];
  var editingChannelId = null;
  async function loadChannels() {
    allChannels = await api('/api/channels');
    renderChannels();
  }
  function renderChannels() {
    var rows = pageRows('channels', allChannels, renderChannels, 10);
    document.getElementById('channels-list').innerHTML = rows.length
      ? rows
          .map(function (x) {
            return (
              '<div class="list-item"><div><strong>#' +
              x.id +
              ' ' +
              esc(x.name) +
              '</strong><small>' +
              esc(channelTypeLabels[x.channel_type] || x.channel_type) +
              ' · ' +
              esc(x.template_name) +
              ' · 已关联 ' +
              x.rule_count +
              ' 条规则' +
              '</small></div><div class="actions"><button class="action-edit" onclick="kpEditChannel(' +
              x.id +
              ')">编辑</button><button class="action-test" onclick="kpTestChannel(' +
              x.id +
              ')">测试</button><button class="action-danger" onclick="kpDeleteChannel(' +
              x.id +
              ')">删除</button></div></div>'
            );
          })
          .join('')
      : '<p class="muted">暂无通知渠道</p>';
  }
  window.kpTestChannel = async function (id) {
    try {
      await api('/api/channels/' + id + '/test', { method: 'POST' });
      toast('测试通知已发送');
    } catch (e) {
      toast(e.message, true);
    }
  };
  window.kpEditChannel = async function (id) {
    try {
      var channel = await api('/api/channels/' + id + '/config');
      editingChannelId = id;
      channelForm.reset();
      document.getElementById('channel-modal-title').textContent = '编辑通知渠道';
      channelForm.elements.name.value = channel.name;
      channelForm.elements.channel_type.value = channel.channel_type;
      var connectionConfig = Object.assign({}, channel.config);
      delete connectionConfig.notification_template_id;
      delete connectionConfig.template_name;
      delete connectionConfig.title_template;
      delete connectionConfig.message_template;
      channelForm.elements.config.value = JSON.stringify(connectionConfig, null, 2);
      await loadChannelTemplateOptions(channel.config.notification_template_id);
      document.getElementById('channel-modal').showModal();
    } catch (err) {
      toast(err.message, true);
    }
  };
  window.kpDeleteChannel = async function (id) {
    if (!confirm('确认删除渠道？关联规则会自动解除该渠道。')) return;
    try {
      await api('/api/channels/' + id, { method: 'DELETE' });
      loadChannels();
    } catch (e) {
      toast(e.message, true);
    }
  };
  channelForm.addEventListener('submit', async function (e) {
      e.preventDefault();
      var f = new FormData(e.target);
      try {
        var config = JSON.parse(f.get('config'));
        var templateId = f.get('notification_template_id');
        config.notification_template_id = Number(templateId);
        await api(editingChannelId ? '/api/channels/' + editingChannelId : '/api/channels', {
          method: editingChannelId ? 'PUT' : 'POST',
          body: JSON.stringify({
            name: f.get('name'),
            channel_type: f.get('channel_type'),
            config: config,
            enabled: true,
          }),
        });
        e.target.closest('dialog').close();
        toast(editingChannelId ? '通知渠道已更新' : '通知渠道已保存');
        editingChannelId = null;
        loadChannels();
      } catch (err) {
        toast(err.message, true);
      }
    });
  async function loadNotificationTemplates() {
    allNotificationTemplates = await api('/api/notification-templates');
    renderNotificationTemplates();
  }
  function renderNotificationTemplates() {
    var rows = pageRows('notification-templates', allNotificationTemplates, renderNotificationTemplates, 10);
    document.getElementById('notification-templates-list').innerHTML = rows.length
      ? rows.map(function (item) {
          var builtIn = item.created_by === 'system';
          return '<div class="artifact-card template-card template-summary-card"><div class="template-card-head"><div><strong>' +
            esc(item.name) + '</strong><small>' + esc(channelTypeLabels[item.channel_type] || item.channel_type) +
            '</small></div>' + (builtIn ? '<span class="badge ok">内置可用</span>' : '<span class="badge">自定义</span>') +
            '</div><div class="template-compact-preview"><span>标题模板</span><code>' + esc(item.title_template) +
            '</code><span>消息模板</span><p>' + esc(item.message_template).replace(/\n/g, ' · ') +
            '</p></div><div class="template-card-foot"><span>可直接关联通知渠道</span><div class="actions">' +
            '<button class="action-info" onclick="kpShowNotificationTemplate(' + item.id + ')">查看详情</button>' +
            (currentIsAdmin && !builtIn ? '<button class="action-danger" onclick="kpDeleteNotificationTemplate(' + item.id + ')">删除</button>' : '') +
            '</div>' +
            '</div></div>';
        }).join('')
      : '<p class="muted">暂无通知模板</p>';
  }
  window.kpShowNotificationTemplate = function (id) {
    var item = allNotificationTemplates.find(function (row) { return row.id === id; });
    if (!item) return;
    var builtIn = item.created_by === 'system';
    document.getElementById('notification-template-detail-title').textContent = item.name;
    document.getElementById('notification-template-detail-meta').textContent = '模板 ID ' + item.id + ' · ' + formatDate(item.created_at);
    document.getElementById('notification-template-detail-overview').innerHTML =
      '<div><span>渠道类型</span><strong>' + esc(channelTypeLabels[item.channel_type] || item.channel_type) + '</strong></div>' +
      '<div><span>模板来源</span><strong>' + (builtIn ? '系统内置' : '自定义') + '</strong></div>' +
      '<div><span>创建人</span><strong>' + esc(item.created_by || '-') + '</strong></div>';
    document.getElementById('notification-template-detail-subject').textContent = item.title_template;
    document.getElementById('notification-template-detail-content').textContent = item.message_template;
    document.getElementById('notification-template-detail-modal').showModal();
  };
  window.kpDeleteNotificationTemplate = async function (id) {
    if (!confirm('确认删除通知模板？正在被通知渠道使用的模板不能删除。')) return;
    try {
      await api('/api/notification-templates/' + id, { method: 'DELETE' });
      toast('通知模板已删除');
      loadNotificationTemplates();
    } catch (err) { toast(err.message, true); }
  };
  var notificationTemplatePresets = {
    email: {
      name: '邮件标准告警模板',
      summary: '适合邮件通知，内容只保留一次告警对象、触发时间和详细指标。',
      title: '[KubePilot][{severity}] {rule_name}',
      message: '告警对象：{target}\n触发时间：{started_at}\n\n{message}',
    },
    feishu: {
      name: '飞书标准告警模板',
      summary: '适合飞书机器人，信息紧凑，移动端查看清晰。',
      title: '[KubePilot][{severity}] {rule_name}',
      message: '告警对象：{target}\n触发时间：{started_at}\n\n{message}',
    },
    dingtalk: {
      name: '钉钉标准告警模板',
      summary: '适合钉钉机器人，突出告警对象、当前值和触发阈值。',
      title: '[KubePilot][{severity}] {rule_name}',
      message: '告警对象：{target}\n触发时间：{started_at}\n\n{message}',
    },
    wecom: {
      name: '企业微信标准告警模板',
      summary: '适合企业微信群机器人，保留处理所需的关键上下文。',
      title: '[KubePilot][{severity}] {rule_name}',
      message: '告警对象：{target}\n触发时间：{started_at}\n\n{message}',
    },
    phone_webhook: {
      name: '电话语音告警模板',
      summary: '适合电话语音网关，内容精简，便于直接播报。',
      title: '{severity}告警：{rule_name}',
      message: '{resource_kind}{resource_name}触发{rule_name}，当前值{value}，阈值{operator}{threshold}，请及时处理。',
    },
  };
  var notificationTemplateForm = document.getElementById('notification-template-form');
  function uniqueNotificationTemplateName(base) {
    var names = allNotificationTemplates.map(function (item) { return item.name; });
    if (names.indexOf(base) === -1) return base;
    var index = 2;
    while (names.indexOf(base + ' ' + index) !== -1) index += 1;
    return base + ' ' + index;
  }
  function applyNotificationTemplatePreset() {
    var type = notificationTemplateForm.elements.preset.value || notificationTemplateForm.elements.channel_type.value;
    var preset = notificationTemplatePresets[type];
    notificationTemplateForm.elements.preset.value = type;
    notificationTemplateForm.elements.channel_type.value = type;
    notificationTemplateForm.elements.name.value = uniqueNotificationTemplateName(preset.name);
    notificationTemplateForm.elements.title_template.value = preset.title;
    notificationTemplateForm.elements.message_template.value = preset.message;
    document.getElementById('notification-template-preset-summary').textContent = preset.summary;
  }
  function prepareNotificationTemplateModal() {
    notificationTemplateForm.reset();
    notificationTemplateForm.elements.preset.value = 'email';
    notificationTemplateForm.elements.channel_type.value = 'email';
    applyNotificationTemplatePreset();
  }
  notificationTemplateForm.elements.preset.addEventListener('change', function () {
    applyNotificationTemplatePreset();
  });
  notificationTemplateForm.elements.channel_type.addEventListener('change', function () {
    notificationTemplateForm.elements.preset.value = notificationTemplateForm.elements.channel_type.value;
    applyNotificationTemplatePreset();
  });
  notificationTemplateForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(event.target);
    try {
      await api('/api/notification-templates', { method: 'POST', body: JSON.stringify({
        name: data.get('name'), channel_type: data.get('channel_type'),
        title_template: data.get('title_template'), message_template: data.get('message_template'),
      }) });
      event.target.closest('dialog').close();
      toast('通知模板已保存');
      loadNotificationTemplates();
    } catch (err) { toast(err.message, true); }
  });

  var allNotificationRecords = [];
  var notificationRecordSelectedIds = new Set();
  var currentNotificationRecordRows = [];
  async function loadNotificationRecords() {
    allNotificationRecords = await api('/api/notification-records?limit=1000');
    notificationRecordSelectedIds.clear();
    renderNotificationRecords();
  }
  function updateNotificationRecordSelection() {
    var count = notificationRecordSelectedIds.size;
    document.getElementById('notification-record-selected-count').textContent = '已选择 ' + count + ' 条';
    document.getElementById('notification-record-batch-delete').disabled = count === 0;
    var selectAll = document.getElementById('notification-record-select-all');
    var selectedOnPage = currentNotificationRecordRows.filter(function (item) {
      return notificationRecordSelectedIds.has(item.id);
    }).length;
    selectAll.checked = currentNotificationRecordRows.length > 0 && selectedOnPage === currentNotificationRecordRows.length;
    selectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < currentNotificationRecordRows.length;
  }
  function renderNotificationRecords() {
    var rows = pageRows('notification-records', allNotificationRecords, renderNotificationRecords, 10);
    currentNotificationRecordRows = rows;
    document.getElementById('notification-records-body').innerHTML = rows.length
      ? rows.map(function (item) {
          var result = item.status === 'sent' ? '<span class="badge ok">发送成功</span>' : '<span class="badge bad">发送失败</span>';
          return '<tr>' + (currentIsAdmin ? '<td class="check-column"><input type="checkbox" aria-label="选择通知记录 ' + item.id + '" ' +
            (notificationRecordSelectedIds.has(item.id) ? 'checked' : '') + ' onchange="kpToggleNotificationRecord(' + item.id + ',this.checked)"></td>' : '') +
            '<td>' + esc(formatDate(item.created_at)) + '</td><td>' + esc(item.channel_name) +
            '</td><td>' + esc(channelTypeLabels[item.channel_type] || item.channel_type) + '</td><td>' +
            esc(item.title) + '</td><td>' + result + '</td><td><div class="long-content compact-content">' + esc(item.error || '—') +
            '</div></td>' + (currentIsAdmin ? '<td><button class="action-danger" onclick="kpDeleteNotificationRecord(' + item.id + ')">删除</button></td>' : '') + '</tr>';
        }).join('')
      : '<tr><td colspan="' + (currentIsAdmin ? '8' : '6') + '" class="muted">暂无通知记录</td></tr>';
    updateNotificationRecordSelection();
  }
  window.kpToggleNotificationRecord = function (id, checked) {
    if (checked) notificationRecordSelectedIds.add(id);
    else notificationRecordSelectedIds.delete(id);
    updateNotificationRecordSelection();
  };
  document.getElementById('notification-record-select-all').onchange = function (event) {
    currentNotificationRecordRows.forEach(function (item) {
      if (event.target.checked) notificationRecordSelectedIds.add(item.id);
      else notificationRecordSelectedIds.delete(item.id);
    });
    renderNotificationRecords();
  };
  window.kpDeleteNotificationRecord = async function (id) {
    if (!confirm('确认删除这条通知记录？')) return;
    try {
      await api('/api/notification-records/' + id, { method: 'DELETE' });
      notificationRecordSelectedIds.delete(id);
      toast('通知记录已删除');
      loadNotificationRecords();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('notification-record-batch-delete').onclick = async function () {
    var ids = Array.from(notificationRecordSelectedIds);
    if (!ids.length || !confirm('确认删除选中的 ' + ids.length + ' 条通知记录？')) return;
    try {
      var result = await api('/api/notification-records/batch', {
        method: 'DELETE', body: JSON.stringify({ ids: ids }),
      });
      toast('已删除 ' + result.deleted + ' 条通知记录');
      loadNotificationRecords();
    } catch (err) { toast(err.message, true); }
  };

  var allOnCallShifts = [];
  var editingOnCallId = null;
  var onCallMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);
  var onCallSelectedDate = new Date();
  function toDateTimeLocal(date) {
    var value = date instanceof Date ? date : new Date(date);
    var parts = new Intl.DateTimeFormat('en-CA', {
      year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
      hourCycle: 'h23', timeZone: displayTimezone,
    }).formatToParts(value).reduce(function (result, item) {
      result[item.type] = item.value;
      return result;
    }, {});
    return parts.year + '-' + parts.month + '-' + parts.day + 'T' + parts.hour + ':' + parts.minute;
  }
  async function prepareOnCallModal(item) {
    var form = document.getElementById('on-call-form');
    form.reset();
    editingOnCallId = item ? item.id : null;
    document.getElementById('on-call-modal-title').textContent = item ? '编辑值班安排' : '新增值班安排';
    var users = (await api('/api/users')).filter(function (user) { return user.is_active; });
    replaceOptions(form.elements.user_id, users.map(function (user) {
      return { value: user.id, label: (user.display_name || user.username) + '（' + user.username + '）' + (user.phone ? ' · ' + user.phone : ' · 未填写手机号') };
    }));
    var start = new Date();
    start.setSeconds(0, 0);
    var end = new Date(start.getTime() + 8 * 60 * 60 * 1000);
    if (item) {
      form.elements.user_id.value = String(item.user_id);
      form.elements.starts_at.value = toDateTimeLocal(item.starts_at);
      form.elements.ends_at.value = toDateTimeLocal(item.ends_at);
      form.elements.note.value = item.note || '';
    } else {
      form.elements.starts_at.value = toDateTimeLocal(start);
      form.elements.ends_at.value = toDateTimeLocal(end);
    }
  }
  async function loadOnCall() {
    allOnCallShifts = await api('/api/on-call-shifts');
    var filter = document.getElementById('on-call-user-filter');
    var selected = filter.value;
    var people = Array.from(new Map(allOnCallShifts.map(function (item) {
      return [String(item.user_id), item.display_name || item.username];
    })).entries()).map(function (entry) { return { value: entry[0], label: entry[1] }; });
    replaceOptions(filter, [{ value: '', label: '全部人员' }].concat(people));
    filter.value = selected;
    renderOnCall();
  }
  function localDateKey(date) {
    return date.getFullYear() + '-' + String(date.getMonth() + 1).padStart(2, '0') + '-' + String(date.getDate()).padStart(2, '0');
  }
  function shiftsForDay(date, shifts) {
    var start = new Date(date.getFullYear(), date.getMonth(), date.getDate());
    var end = new Date(start.getTime() + 86400000);
    return shifts.filter(function (item) {
      return new Date(item.starts_at) < end && new Date(item.ends_at) > start;
    }).sort(function (a, b) { return new Date(a.starts_at) - new Date(b.starts_at); });
  }
  function onCallColorClass(userId) {
    return 'shift-color-' + (Number(userId || 0) % 6);
  }
  function renderOnCall() {
    var userFilter = document.getElementById('on-call-user-filter').value;
    var shifts = allOnCallShifts.filter(function (item) { return !userFilter || String(item.user_id) === userFilter; });
    var monthStart = new Date(onCallMonth.getFullYear(), onCallMonth.getMonth(), 1);
    var monthEnd = new Date(onCallMonth.getFullYear(), onCallMonth.getMonth() + 1, 1);
    var gridStart = new Date(monthStart);
    gridStart.setDate(gridStart.getDate() - ((gridStart.getDay() + 6) % 7));
    document.getElementById('on-call-month-title').textContent = onCallMonth.getFullYear() + ' 年 ' + (onCallMonth.getMonth() + 1) + ' 月';
    var calendar = '';
    var coveredDays = new Set();
    for (var index = 0; index < 42; index += 1) {
      var date = new Date(gridStart);
      date.setDate(gridStart.getDate() + index);
      var dayShifts = shiftsForDay(date, shifts);
      if (date >= monthStart && date < monthEnd && dayShifts.length) coveredDays.add(localDateKey(date));
      var key = localDateKey(date);
      var isToday = key === localDateKey(new Date());
      var selected = key === localDateKey(onCallSelectedDate);
      calendar += '<button type="button" class="on-call-day ' + (date.getMonth() === onCallMonth.getMonth() ? '' : 'outside ') +
        (isToday ? 'today ' : '') + (selected ? 'selected' : '') + '" onclick="kpSelectOnCallDay(\'' + key + '\')">' +
        '<span class="day-number">' + date.getDate() + '</span><div class="day-shifts">' +
        dayShifts.slice(0, 3).map(function (item) {
          var start = new Date(item.starts_at);
          return '<span class="day-shift ' + onCallColorClass(item.user_id) + '"><b>' + esc(item.display_name || item.username) +
            '</b><small>' + formatClock(start) + '</small></span>';
        }).join('') + (dayShifts.length > 3 ? '<span class="day-shift-more">+' + (dayShifts.length - 3) + ' 人</span>' : '') +
        '</div></button>';
    }
    document.getElementById('on-call-calendar').innerHTML = calendar;
    var monthShifts = shifts.filter(function (item) {
      return new Date(item.starts_at) < monthEnd && new Date(item.ends_at) > monthStart;
    });
    var peopleCount = new Set(monthShifts.map(function (item) { return item.user_id; })).size;
    document.getElementById('on-call-summary').innerHTML = '<span><strong>' + peopleCount + '</strong> 位人员</span>' +
      '<span><strong>' + monthShifts.length + '</strong> 个班次</span><span><strong>' + coveredDays.size + '</strong> 天覆盖</span>';
    var agendaRows = shiftsForDay(onCallSelectedDate, shifts);
    document.getElementById('on-call-agenda-title').textContent = (onCallSelectedDate.getMonth() + 1) + ' 月 ' + onCallSelectedDate.getDate() + ' 日排班';
    document.getElementById('on-call-list').innerHTML = agendaRows.length ? agendaRows.map(function (item) {
      var phone = item.phone
        ? '<a class="agenda-phone" href="tel:' + encodeURIComponent(item.phone) + '" title="拨打 ' + esc(item.phone) + '">☎ ' + esc(item.phone) + '</a>'
        : '<span class="agenda-phone missing">未填写手机号</span>';
      return '<article class="agenda-shift ' + onCallColorClass(item.user_id) + '"><div class="agenda-time"><strong>' +
        formatClock(item.starts_at) +
        '</strong><span>至</span><strong>' + formatClock(item.ends_at) +
        '</strong></div><div class="agenda-info"><strong>' + esc(item.display_name || item.username) + '</strong><small>' + esc(item.username) + '</small>' + phone + '<p>' + esc(item.note || '常规值班') +
        '</p></div>' + (currentIsAdmin ? '<div class="agenda-actions"><button class="action-edit" onclick="kpEditOnCall(' + item.id +
        ')">编辑</button><button class="action-danger" onclick="kpDeleteOnCall(' + item.id + ')">删除</button></div>' : '') + '</article>';
    }).join('') : '<div class="on-call-empty"><strong>当日暂无排班</strong><span>可选择其他日期查看</span></div>';
  }
  window.kpSelectOnCallDay = function (key) {
    var parts = key.split('-').map(Number);
    onCallSelectedDate = new Date(parts[0], parts[1] - 1, parts[2]);
    if (onCallSelectedDate.getMonth() !== onCallMonth.getMonth()) {
      onCallMonth = new Date(onCallSelectedDate.getFullYear(), onCallSelectedDate.getMonth(), 1);
    }
    renderOnCall();
  };
  document.getElementById('on-call-prev').onclick = function () {
    onCallMonth = new Date(onCallMonth.getFullYear(), onCallMonth.getMonth() - 1, 1);
    onCallSelectedDate = new Date(onCallMonth);
    renderOnCall();
  };
  document.getElementById('on-call-next').onclick = function () {
    onCallMonth = new Date(onCallMonth.getFullYear(), onCallMonth.getMonth() + 1, 1);
    onCallSelectedDate = new Date(onCallMonth);
    renderOnCall();
  };
  document.getElementById('on-call-today').onclick = function () {
    onCallSelectedDate = new Date();
    onCallMonth = new Date(onCallSelectedDate.getFullYear(), onCallSelectedDate.getMonth(), 1);
    renderOnCall();
  };
  document.getElementById('on-call-user-filter').onchange = renderOnCall;
  window.kpEditOnCall = async function (id) {
    var item = allOnCallShifts.find(function (row) { return row.id === id; });
    if (!item) return;
    try {
      await prepareOnCallModal(item);
      document.getElementById('on-call-modal').showModal();
    } catch (err) { toast(err.message, true); }
  };
  window.kpDeleteOnCall = async function (id) {
    if (!confirm('确认删除值班安排？')) return;
    try {
      await api('/api/on-call-shifts/' + id, { method: 'DELETE' });
      toast('值班安排已删除');
      loadOnCall();
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('on-call-form').addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(event.target);
    var payload = {
      user_id: Number(data.get('user_id')), starts_at: data.get('starts_at'),
      ends_at: data.get('ends_at'), note: data.get('note'),
    };
    try {
      await api(editingOnCallId ? '/api/on-call-shifts/' + editingOnCallId : '/api/on-call-shifts', {
        method: editingOnCallId ? 'PUT' : 'POST', body: JSON.stringify(payload),
      });
      event.target.closest('dialog').close();
      toast(editingOnCallId ? '值班安排已更新' : '值班安排已保存');
      editingOnCallId = null;
      loadOnCall();
    } catch (err) { toast(err.message, true); }
  });

  async function loadRoles() {
    var roles = await api('/api/roles');
    document.getElementById('roles-list').innerHTML = roles.map(function (role) {
      return '<div class="artifact-card"><strong>' + esc(role.name) + '</strong><span class="tag">' +
        esc(role.code) + '</span><p class="muted">' + esc(role.description) + '</p></div>';
    }).join('');
  }
  var permissionCatalog = null;
  var permissionUsers = [];
  var selectedPermissionUserId = null;
  async function loadPermissions() {
    if (!currentIsAdmin) throw new Error('只有管理员可以管理菜单权限');
    var data = await Promise.all([api('/api/permissions/catalog'), api('/api/users')]);
    permissionCatalog = data[0];
    permissionUsers = data[1];
    renderPermissionUsers();
    if (selectedPermissionUserId && permissionUsers.some(function (item) { return item.id === selectedPermissionUserId; })) {
      await selectPermissionUser(selectedPermissionUserId);
    }
  }
  function renderPermissionUsers() {
    var keyword = document.getElementById('permission-user-search').value.trim().toLowerCase();
    var rows = permissionUsers.filter(function (user) {
      return !keyword || ((user.display_name || '') + ' ' + user.username + ' ' + user.role_name).toLowerCase().includes(keyword);
    });
    document.getElementById('permission-users').innerHTML = rows.length ? rows.map(function (user) {
      return '<button type="button" class="permission-user ' + (user.id === selectedPermissionUserId ? 'active' : '') +
        '" onclick="kpSelectPermissionUser(' + user.id + ')"><span class="permission-avatar">' +
        esc((user.display_name || user.username).slice(0, 1).toUpperCase()) + '</span><span><strong>' + esc(user.display_name || user.username) +
        '</strong><small>' + esc(user.username) + ' · ' + esc(user.role_name) + (user.is_active ? ' · 已启用' : ' · 已停用') +
        '</small></span></button>';
    }).join('') : '<div class="permission-empty compact">没有匹配用户</div>';
  }
  async function selectPermissionUser(id) {
    selectedPermissionUserId = Number(id);
    renderPermissionUsers();
    var detail = await api('/api/users/' + id + '/menu-permissions');
    document.getElementById('permission-editor-empty').classList.add('hidden');
    document.getElementById('permission-editor').classList.remove('hidden');
    document.getElementById('permission-username').textContent = detail.display_name || detail.username;
    document.getElementById('permission-user-meta').textContent = detail.username + ' · ' + detail.role_name +
      (detail.is_admin ? ' · 系统固定全部权限' : detail.is_custom ? ' · 使用用户自定义权限' : ' · 使用角色默认权限');
    var selected = new Set(detail.menu_permissions);
    document.getElementById('permission-groups').innerHTML = permissionCatalog.groups.map(function (group) {
      var checkedCount = group.items.filter(function (item) { return selected.has(item.code); }).length;
      return '<details class="permission-tree-node" open><summary><span class="permission-tree-arrow">›</span><label onclick="event.stopPropagation()"><input type="checkbox" data-permission-group="' +
        esc(group.code) + '" ' + (checkedCount === group.items.length ? 'checked' : '') + (detail.is_admin ? ' disabled' : '') +
        '><span>' + esc(group.name) + '</span></label><small>' + checkedCount + '/' + group.items.length + '</small></summary><div class="permission-tree-children">' +
        group.items.map(function (item) { return '<label class="permission-tree-leaf"><span class="permission-tree-branch"></span><input type="checkbox" name="menu_permission" data-group="' +
          esc(group.code) + '" value="' + esc(item.code) + '" ' + (selected.has(item.code) ? 'checked' : '') +
          (detail.is_admin ? ' disabled' : '') + '><span>' + esc(item.name) + '</span><code>' + esc(item.code) + '</code></label>'; }).join('') + '</div></details>';
    }).join('');
    document.getElementById('permission-select-all').disabled = detail.is_admin;
    document.getElementById('permission-reset').disabled = detail.is_admin;
    document.getElementById('permission-save').disabled = detail.is_admin;
    document.querySelectorAll('[data-permission-group]').forEach(function (checkbox) {
      var initialChildren = Array.from(document.querySelectorAll('input[name="menu_permission"][data-group="' + checkbox.dataset.permissionGroup + '"]'));
      var initialChecked = initialChildren.filter(function (item) { return item.checked; }).length;
      checkbox.indeterminate = initialChecked > 0 && initialChecked < initialChildren.length;
      checkbox.onchange = function () {
        var children = document.querySelectorAll('input[name="menu_permission"][data-group="' + checkbox.dataset.permissionGroup + '"]');
        children.forEach(function (item) {
          item.checked = checkbox.checked;
        });
        checkbox.indeterminate = false;
        checkbox.closest('summary').querySelector('small').textContent = (checkbox.checked ? children.length : 0) + '/' + children.length;
      };
    });
    document.querySelectorAll('input[name="menu_permission"]').forEach(function (checkbox) {
      checkbox.onchange = function () {
        var group = checkbox.dataset.group;
        var children = Array.from(document.querySelectorAll('input[name="menu_permission"][data-group="' + group + '"]'));
        var parent = document.querySelector('[data-permission-group="' + group + '"]');
        var checked = children.filter(function (item) { return item.checked; }).length;
        parent.checked = checked === children.length;
        parent.indeterminate = checked > 0 && checked < children.length;
        var count = parent.closest('summary').querySelector('small');
        count.textContent = checked + '/' + children.length;
      };
    });
  }
  window.kpSelectPermissionUser = function (id) {
    selectPermissionUser(id).catch(function (err) { toast(err.message, true); });
  };
  document.getElementById('permission-user-search').oninput = renderPermissionUsers;
  document.getElementById('permission-select-all').onclick = function () {
    document.querySelectorAll('#permission-groups input[type=checkbox]').forEach(function (item) { item.checked = true; item.indeterminate = false; });
    document.querySelectorAll('[data-permission-group]').forEach(function (item) {
      var children = document.querySelectorAll('input[name="menu_permission"][data-group="' + item.dataset.permissionGroup + '"]');
      item.closest('summary').querySelector('small').textContent = children.length + '/' + children.length;
    });
  };
  document.getElementById('permission-save').onclick = async function () {
    if (!selectedPermissionUserId) return;
    var selected = Array.from(document.querySelectorAll('input[name="menu_permission"]:checked')).map(function (item) { return item.value; });
    try {
      await api('/api/users/' + selectedPermissionUserId + '/menu-permissions', { method: 'PUT', body: JSON.stringify({ menu_permissions: selected }) });
      toast('菜单权限已保存');
      await selectPermissionUser(selectedPermissionUserId);
    } catch (err) { toast(err.message, true); }
  };
  document.getElementById('permission-reset').onclick = async function () {
    if (!selectedPermissionUserId || !confirm('确认恢复为当前角色的默认菜单权限？')) return;
    try {
      await api('/api/users/' + selectedPermissionUserId + '/menu-permissions', { method: 'DELETE' });
      toast('已恢复角色默认权限');
      await selectPermissionUser(selectedPermissionUserId);
    } catch (err) { toast(err.message, true); }
  };
  var allUsers = [];
  var editingUserId = null;
  var userForm = document.getElementById('user-form');
  async function loadUsers() {
    allUsers = await api('/api/users');
    renderUsers();
  }
  function renderUsers() {
    var rows = pageRows('users', allUsers, renderUsers, 10);
    document.getElementById('users-body').innerHTML = rows.length
      ? rows.map(function (user) {
          var createdAt = formatDate(user.created_at);
          return '<tr><td><div class="user-identity"><strong>' + esc(user.display_name || user.username) + '</strong><small>' + esc(user.username) + '</small></div></td><td>' +
            esc(user.email) + '</td><td>' + esc(user.phone || '未填写') + '</td><td>' +
            '<span class="badge ' + (user.is_admin ? 'warn' : '') + '">' + esc(user.role_name || user.role) + '</span>' +
            (user.is_builtin_admin ? ' <span class="badge ok">系统内置</span>' : '') +
            '</td><td>' + (user.is_active ? '<span class="badge ok">启用</span>' : '<span class="badge bad">停用</span>') +
            (user.must_change_password ? ' <span class="badge warn">待首次改密</span>' : '') +
            '</td><td>' + esc(createdAt) + '</td><td><div class="actions"><button class="action-edit" onclick="kpEditUser(' +
            user.id + ')">编辑</button>' + (user.is_builtin_admin
              ? '<button class="action-disabled" disabled title="内置 admin 管理员不能删除">系统保护</button>'
              : '<button class="action-danger" onclick="kpDeleteUser(' + user.id + ')">删除</button>') +
            '</div></td></tr>';
        }).join('')
      : '<tr><td colspan="7" class="muted">暂无人员</td></tr>';
  }
  function prepareUserModal() {
    editingUserId = null;
    userForm.reset();
    userForm.elements.username.disabled = false;
    userForm.elements.role.disabled = false;
    userForm.elements.is_active.disabled = false;
    userForm.elements.password.required = true;
    userForm.elements.is_active.checked = true;
    userForm.elements.role.value = 'operator';
    document.getElementById('user-modal-title').textContent = '新增人员';
    document.getElementById('user-password-label').textContent = '初始密码';
    document.getElementById('user-form-hint').textContent = '新用户首次登录必须修改初始密码；初始密码和首次设置的新密码都需符合强度要求：' + passwordPolicyHint;
  }
  window.kpEditUser = function (id) {
    var user = allUsers.find(function (item) { return item.id === id; });
    if (!user) return;
    editingUserId = id;
    userForm.reset();
    userForm.elements.username.value = user.username;
    userForm.elements.username.disabled = true;
    userForm.elements.display_name.value = user.display_name || user.username;
    userForm.elements.email.value = user.email;
    userForm.elements.phone.value = user.phone || '';
    userForm.elements.password.required = false;
    userForm.elements.password.value = '';
    userForm.elements.role.value = user.role || (user.is_admin ? 'admin' : 'operator');
    userForm.elements.is_active.checked = user.is_active;
    userForm.elements.role.disabled = user.is_builtin_admin;
    userForm.elements.is_active.disabled = user.is_builtin_admin;
    document.getElementById('user-modal-title').textContent = '编辑人员';
    document.getElementById('user-password-label').textContent = '重置密码（留空不修改）';
    document.getElementById('user-form-hint').textContent = (user.is_builtin_admin
      ? '内置 admin 管理员受系统保护：可以修改邮箱或密码，但不能停用、取消管理员权限或删除。'
      : '不能停用当前账号，且系统必须保留至少一个启用的管理员。') + ' 重置密码时需符合强度要求：' + passwordPolicyHint;
    document.getElementById('user-modal').showModal();
  };
  window.kpDeleteUser = async function (id) {
    var user = allUsers.find(function (item) { return item.id === id; });
    var username = user ? user.username : '#' + id;
    if (!confirm('确认删除人员 ' + username + '？')) return;
    try {
      await api('/api/users/' + id, { method: 'DELETE' });
      toast('人员已删除');
      loadUsers();
    } catch (err) {
      toast(err.message, true);
    }
  };
  userForm.addEventListener('submit', async function (event) {
    event.preventDefault();
    var data = new FormData(userForm);
    var password = String(data.get('password') || '');
    if (password) {
      var policyError = passwordPolicyError(password);
      if (policyError) {
        toast(policyError, true);
        userForm.elements.password.focus();
        return;
      }
    }
    var payload = {
      display_name: String(data.get('display_name') || '').trim(),
      email: data.get('email'),
      phone: String(data.get('phone') || '').trim(),
      role: data.get('role') || 'operator',
      is_active: data.get('is_active') === 'on',
    };
    if (editingUserId) {
      var editingUser = allUsers.find(function (item) { return item.id === editingUserId; });
      if (editingUser && editingUser.is_builtin_admin) {
        payload.role = 'admin';
        payload.is_active = true;
      }
      payload.password = password || null;
    } else {
      payload.username = data.get('username');
      payload.password = password;
    }
    try {
      await api(editingUserId ? '/api/users/' + editingUserId : '/api/users', {
        method: editingUserId ? 'PUT' : 'POST',
        body: JSON.stringify(payload),
      });
      userForm.closest('dialog').close();
      toast(editingUserId ? '人员信息已更新' : '人员已创建');
      editingUserId = null;
      loadUsers();
    } catch (err) {
      toast(err.message, true);
    }
  });

  var allAuditLogs = [];
  var auditSelectedIds = new Set();
  var currentAuditPageRows = [];
  var auditActionLabels = {
    'auth.login': '登录平台', 'auth.login.failed': '登录失败', 'auth.logout': '退出平台',
    'auth.password_change': '修改密码', 'auth.password_change.failed': '修改密码失败',
    'user.create': '创建用户', 'user.update': '更新用户', 'user.delete': '删除用户',
    'user.permissions.update': '分配菜单权限', 'user.permissions.reset': '恢复角色权限',
    'deployment.apply': '更新发布', 'deployment.rollback': '版本回滚',
    'deployment.scale': '调整副本', 'deployment.restart': '重启应用', 'pod.restart': '重启 Pod',
    'image.build': '构建镜像', 'image.build.delete': '删除构建记录',
    'build_config.create': '创建构建配置', 'build_config.update': '更新构建配置',
    'build_config.delete': '删除构建配置', 'k8s_resource.update': '更新资源配置',
    'k8s_resource.rollback': '回退资源配置', 'alert_rule.create': '创建告警规则',
    'alert_rule.update': '更新告警规则', 'alert_rule.delete': '删除告警规则',
    'alert_subscription.create': '创建告警订阅', 'alert_subscription.update': '更新告警订阅',
    'alert_subscription.delete': '删除告警订阅',
    'on_call_shift.create': '创建值班安排', 'on_call_shift.update': '更新值班安排',
    'on_call_shift.delete': '删除值班安排',
    'notification_record.delete': '删除通知记录',
    'notification_record.batch_delete': '批量删除通知记录',
    'incident.acknowledge': '确认故障', 'incident.update': '更新故障处理',
    'channel.create': '创建通知渠道', 'channel.update': '更新通知渠道',
    'channel.delete': '删除通知渠道', 'channel.test': '测试通知渠道',
    'alert_event.delete': '删除告警事件',
    'audit_log.delete': '删除审计日志', 'audit_log.batch_delete': '批量删除审计日志',
    'audit_log.clear': '清空审计日志',
  };
  async function loadAuditLogs() {
    allAuditLogs = await api('/api/audit-logs?limit=2000');
    auditSelectedIds.clear();
    renderAuditLogs();
  }
  function auditActionTone(action) {
    if (action.includes('failed') || action.includes('delete') || action.includes('clear')) return 'danger';
    if (action.includes('create') || action.includes('login')) return 'success';
    return 'info';
  }
  function updateAuditSelectionUI() {
    var count = auditSelectedIds.size;
    document.getElementById('audit-selected-count').textContent = '已选择 ' + count + ' 条';
    document.getElementById('audit-batch-delete').disabled = count === 0;
    var selectAll = document.getElementById('audit-select-all');
    var selectedOnPage = currentAuditPageRows.filter(function (item) { return auditSelectedIds.has(item.id); }).length;
    selectAll.checked = currentAuditPageRows.length > 0 && selectedOnPage === currentAuditPageRows.length;
    selectAll.indeterminate = selectedOnPage > 0 && selectedOnPage < currentAuditPageRows.length;
  }
  function renderAuditLogs() {
    var username = document.getElementById('audit-user-filter').value.trim().toLowerCase();
    var action = document.getElementById('audit-action-filter').value.trim().toLowerCase();
    var filtered = allAuditLogs.filter(function (item) {
      var actionText = (auditActionLabels[item.action] || '') + ' ' + item.action;
      return (!username || (String(item.display_name || '') + ' ' + String(item.username)).toLowerCase().includes(username)) &&
        (!action || actionText.toLowerCase().includes(action));
    });
    document.getElementById('audit-total-count').textContent = filtered.length;
    var rows = pageRows('audit-logs', filtered, renderAuditLogs, 20);
    currentAuditPageRows = rows;
    document.getElementById('audit-logs-body').innerHTML = rows.length ? rows.map(function (item) {
      var hasDetail = item.detail && Object.keys(item.detail).length;
      return '<tr><td class="check-column"><input type="checkbox" aria-label="选择日志 ' + item.id + '" ' +
        (auditSelectedIds.has(item.id) ? 'checked' : '') + ' onchange="kpToggleAuditLog(' + item.id + ',this.checked)"></td><td><span class="audit-time">' +
        esc(formatDate(item.created_at)) + '</span></td><td><div class="audit-user"><span class="audit-avatar">' +
        esc(String(item.display_name || item.username || '?').slice(0, 1).toUpperCase()) + '</span><span><strong>' + esc(item.display_name || item.username) +
        '</strong><small>' + esc(item.username) + '</small></span></div></td><td><span class="audit-action-pill ' + auditActionTone(item.action) + '">' +
        esc(auditActionLabels[item.action] || item.action) + '</span><small class="audit-action-code">' + esc(item.action) +
        '</small></td><td><span class="audit-resource" title="' + esc(item.resource || '-') + '">' + esc(item.resource || '-') +
        '</span></td><td>' + (hasDetail ? '<button class="action-info audit-detail-button" onclick="kpShowAuditDetail(' + item.id + ')">查看详情</button>' : '<span class="muted">—</span>') +
        '</td><td class="operation-column"><button class="action-danger" onclick="kpDeleteAuditLog(' + item.id + ')">删除</button></td></tr>';
    }).join('') : '<tr><td colspan="7" class="audit-empty">暂无符合条件的审计日志</td></tr>';
    updateAuditSelectionUI();
  }
  document.getElementById('audit-user-filter').addEventListener('input', function () {
    if (pagerStates['audit-logs']) pagerStates['audit-logs'].page = 1;
    renderAuditLogs();
  });
  document.getElementById('audit-action-filter').addEventListener('input', function () {
    if (pagerStates['audit-logs']) pagerStates['audit-logs'].page = 1;
    renderAuditLogs();
  });
  window.kpToggleAuditLog = function (id, checked) {
    if (checked) auditSelectedIds.add(id);
    else auditSelectedIds.delete(id);
    updateAuditSelectionUI();
  };
  document.getElementById('audit-select-all').onchange = function (event) {
    currentAuditPageRows.forEach(function (item) {
      if (event.target.checked) auditSelectedIds.add(item.id);
      else auditSelectedIds.delete(item.id);
    });
    renderAuditLogs();
  };
  window.kpShowAuditDetail = function (id) {
    var item = allAuditLogs.find(function (row) { return row.id === id; });
    if (!item) return;
    document.getElementById('audit-detail-title').textContent = auditActionLabels[item.action] || item.action;
    document.getElementById('audit-detail-meta').textContent = formatDate(item.created_at) + ' · 日志 ID ' + item.id;
    document.getElementById('audit-detail-overview').innerHTML =
      '<div><span>操作用户</span><strong>' + esc(item.display_name || item.username) + '（' + esc(item.username) + '）</strong></div>' +
      '<div><span>操作对象</span><strong>' + esc(item.resource || '-') + '</strong></div>' +
      '<div><span>操作类型</span><strong>' + esc(auditActionLabels[item.action] || item.action) + '</strong></div>';
    document.getElementById('audit-detail-content').textContent = JSON.stringify(item.detail || {}, null, 2);
    document.getElementById('audit-detail-modal').showModal();
  };
  window.kpDeleteAuditLog = async function (id) {
    if (!confirm('确认删除这条审计日志？删除操作本身仍会被审计。')) return;
    try {
      await api('/api/audit-logs/' + id, { method: 'DELETE' });
      auditSelectedIds.delete(id);
      toast('审计日志已删除');
      loadAuditLogs();
    } catch (err) {
      toast(err.message, true);
    }
  };
  document.getElementById('audit-batch-delete').onclick = async function () {
    var ids = Array.from(auditSelectedIds);
    if (!ids.length || !confirm('确认批量删除选中的 ' + ids.length + ' 条审计日志？删除操作本身仍会被审计。')) return;
    try {
      var result = await api('/api/audit-logs/batch', {
        method: 'DELETE', body: JSON.stringify({ ids: ids }),
      });
      toast('已批量删除 ' + result.deleted + ' 条审计日志');
      loadAuditLogs();
    } catch (err) {
      toast(err.message, true);
    }
  };
  document.getElementById('audit-clear').onclick = async function () {
    if (!confirm('确认清空全部审计日志？清空操作本身会保留一条新审计记录。')) return;
    try {
      var result = await api('/api/audit-logs', { method: 'DELETE' });
      toast('已清理 ' + result.deleted + ' 条审计日志');
      loadAuditLogs();
    } catch (err) {
      toast(err.message, true);
    }
  };
  if (token) {
    api('/api/me')
      .then(function (x) {
        persistIdentity(x);
        if (x.must_change_password) requirePasswordChange(x);
        else showApp(x.username, x.is_admin, x.role, x.menu_permissions, x.display_name);
      })
      .catch(function () {
        logout(false);
      });
  } else {
    clearLoginForm();
  }
})();
