// ========== 全局状态 ==========
let map = null;
let routeData = null;
let routeName = '';
let forecastMap = {};        // poiId -> {poi, h_grid, sources, daily, hourly, fetched_at}
let selectedPoiId = null;
let selectedHourlyDate = ''; // 选中的 hourly 日 (YYYY-MM-DD)
let poiMarkers = {};
let gearData = null;
let departureDate = '';      // YYYY-MM-DD
let tripDays = 4;            // 徒步天数 1-7, 由用户输入
let addMarkerMode = false;   // 添加自定义标记模式
let tempMarker = null;       // 添加模式下的临时 marker

// 我的路线 (localStorage 持久化, 不依赖登录)
// 结构: [{name, ts}, ...]  name=路线名(后端用 name 索引), ts=上传/查看时间戳
const MY_ROUTES_KEY = 'myRoutes_v1';
// name → id 映射, 删除时需要 id 调 DELETE /api/routes/{id}
// switchRouteByName 拿到 routeData 后自动填充
const routeNameToId = {};

function loadMyRoutes() {
    try {
        return JSON.parse(localStorage.getItem(MY_ROUTES_KEY)) || [];
    } catch (e) {
        console.warn('localStorage 读取失败', e);
        return [];
    }
}

function saveMyRoutes(arr) {
    try {
        localStorage.setItem(MY_ROUTES_KEY, JSON.stringify(arr));
    } catch (e) {
        console.warn('localStorage 写入失败', e);
    }
}

function addMyRoute(name) {
    const arr = loadMyRoutes().filter(r => r.name !== name);
    arr.unshift({name, ts: Date.now()});
    // 只保留最近 20 条, 避免无限膨胀
    saveMyRoutes(arr.slice(0, 20));
    renderMyRoutesSelect();
}

function renderMyRoutesSelect() {
    const sel = document.getElementById('my-routes');
    if (!sel) return;
    const arr = loadMyRoutes();
    if (arr.length === 0) {
        sel.innerHTML = '<option value="">(暂无, 上传 GPX 后自动记录)</option>';
        return;
    }
    sel.innerHTML = arr.map(r => `<option value="${r.name}">${r.name}</option>`).join('');
    if (routeName && arr.some(r => r.name === routeName)) {
        sel.value = routeName;
    }
}

const POI_ICON = {
    camp: '🏕', pass: '⛰', retreat: '⬇', danger: '⚠',
    water: '💧', scenery: '📷', note: '•', custom: '📍',
};
const TYPE_LABEL = {
    camp: '营地', pass: '垭口', retreat: '下撤', danger: '危险',
    water: '水源', scenery: '风景', note: '备注', custom: '自定义',
};

document.addEventListener('DOMContentLoaded', init);

// 从 localStorage 恢复登录态. 未登录强制跳 /login.
window.__token = localStorage.getItem('__token') || null;
window.__username = localStorage.getItem('__username') || null;

async function init() {
    // 登录守卫: 无 token 直接跳登录页
    if (!window.__token) {
        location.href = '/login';
        return;
    }
    const tag = document.getElementById('login-tag');
    if (tag) tag.textContent = '👤 ' + (window.__username || '已登录');
    const logoutBtn = document.getElementById('logout-btn');
    if (logoutBtn) logoutBtn.addEventListener('click', doLogout);

    // 默认出发日 = 今天 + 3 天
    const d = new Date();
    d.setDate(d.getDate() + 3);
    departureDate = d.toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'});
    document.getElementById('departure-date').value = departureDate;
    document.getElementById('departure-date').addEventListener('change', e => {
        departureDate = e.target.value;
        rerenderForDeparture();
    });

    // 天数 input
    const daysInput = document.getElementById('trip-days');
    daysInput.value = tripDays;
    daysInput.addEventListener('change', e => {
        const v = parseInt(e.target.value, 10);
        if (isNaN(v) || v < 1 || v > 16) {
            e.target.value = tripDays;
            return;
        }
        tripDays = v;
        renderPoiTableHeader();
        renderPoiTable();
        Object.values(forecastMap).forEach(item => renderPoiDayBadges(item));
        rerenderForDeparture();
    });

    // 我的路线 select
    renderMyRoutesSelect();
    document.getElementById('my-routes').addEventListener('change', e => {
        if (e.target.value) switchRouteByName(e.target.value);
    });
    document.getElementById('btn-delete-route').addEventListener('click', deleteCurrentRoute);

    // GPX 上传
    document.getElementById('gpx-input').addEventListener('change', e => {
        const f = e.target.files[0];
        if (f) uploadGpx(f);
        e.target.value = '';  // 允许重复上传同名文件
    });

    document.getElementById('btn-export').addEventListener('click', exportPdf);
    document.getElementById('btn-guide').addEventListener('click', downloadGuide);
    document.getElementById('btn-refresh').addEventListener('click', manualRefresh);
    document.getElementById('btn-add-marker').addEventListener('click', toggleAddMarkerMode);
    bindCompareButton();

    // ESC 退出添加模式
    document.addEventListener('keydown', e => {
        if (e.key === 'Escape' && addMarkerMode) exitAddMarkerMode();
    });

    // 周期性拉取系统状态 (每 30s)
    loadStatus();
    setInterval(loadStatus, 30000);

    renderPoiTableHeader();

    try {
        const r = await fetch('/api/routes');
        const routesResp = await r.json();
        const routes = Array.isArray(routesResp) ? routesResp : (routesResp.routes || []);
        if (routes.length === 0) {
            showError('未找到 GPX 路线, 请点击右上角"上传 GPX"');
            return;
        }
        // 优先用 localStorage 记的最近一条, 否则取服务端第一条
        const myArr = loadMyRoutes();
        const initialName = (myArr.length > 0 && routes.some(x => x.name === myArr[0].name))
            ? myArr[0].name
            : routes[0].name;
        await switchRouteByName(initialName);
    } catch (e) {
        showError('初始化失败: ' + e.message);
        console.error(e);
    }
}

async function switchRouteByName(name) {
    routeName = name;
    if (addMarkerMode) exitAddMarkerMode();
    const r2 = await fetch(`/api/route/${encodeURIComponent(routeName)}`);
    if (!r2.ok) {
        showError(`路线加载失败 (${r2.status}): ${routeName}`);
        return;
    }
    routeData = await r2.json();
    // 缓存 name → id 映射, 删除按钮要用
    if (routeData.id) routeNameToId[routeData.name] = routeData.id;
    // 清残留状态: 避免上一条路线的 selectedPoiId / selectedHourlyDate 影响 renderForecastPanel
    forecastMap = {};
    selectedPoiId = null;
    selectedHourlyDate = '';
    addMyRoute(routeName);
    renderHeader(routeData);
    renderMap();
    renderProfile();
    renderPoiTable();
    loadForecast();
    loadGear();
}

async function uploadGpx(file) {
    if (!file.name.toLowerCase().endsWith('.gpx')) {
        showError('只支持 .gpx 文件');
        return;
    }
    if (!window.__token) {
        showError('请先登录: F12 控制台跑 login(), 或手动 localStorage.setItem(\'__token\', <jwt>)');
        return;
    }
    const body = new FormData();
    body.append('file', file);
    // 注意: is_public=true 让 /api/routes 列表也能看到, 否则只能 /api/routes/mine
    body.append('is_public', 'true');
    showError(`⏳ 上传中: ${file.name}`);
    try {
        const r = await fetch('/api/routes/upload', {
            method: 'POST',
            body,
            headers: {'Authorization': 'Bearer ' + window.__token},
        });
        if (r.status === 401 || r.status === 403) {
            showError('登录已过期, 请重新登录');
            return;
        }
        if (!r.ok) {
            const txt = await r.text();
            showError(`上传失败 (${r.status}): ${txt.slice(0, 200)}`);
            return;
        }
        const data = await r.json();
        await switchRouteByName(data.name);
    } catch (e) {
        showError('上传异常: ' + e.message);
        console.error(e);
    }
}

// 退出登录: 清 token 并跳登录页
function doLogout() {
    window.__token = null;
    window.__username = null;
    localStorage.removeItem('__token');
    localStorage.removeItem('__username');
    location.href = '/login';
}

// 删除当前选中路线: name 从 select 拿, id 从 routeNameToId 缓存拿
async function deleteCurrentRoute() {
    const sel = document.getElementById('my-routes');
    const name = sel.value;
    if (!name) {
        showError('请先在「我的路线」中选择一条轨迹');
        return;
    }
    const id = routeNameToId[name];
    if (!id) {
        showError(`未找到路线 id (${name}). 请先切换到该路线后再删, 或刷新页面重试.`);
        return;
    }
    if (!confirm(`确认删除路线「${name}」?\n该操作会一并删除所有 waypoint, 不可恢复.`)) return;

    try {
        const r = await fetch(`/api/routes/${id}`, {
            method: 'DELETE',
            headers: {'Authorization': 'Bearer ' + window.__token},
        });
        const data = await r.json();
        if (!r.ok) {
            showError(`删除失败 (${r.status}): ${data.detail || r.statusText}`);
            return;
        }
        // 从 localStorage 移除
        saveMyRoutes(loadMyRoutes().filter(x => x.name !== name));
        delete routeNameToId[name];
        renderMyRoutesSelect();
        // 切到剩余路线的第一条 (如果有)
        const remaining = loadMyRoutes();
        if (remaining.length > 0) {
            await switchRouteByName(remaining[0].name);
        } else {
            routeData = null;
            routeName = '';
            forecastMap = {};
            showError('已删除当前路线, 且没有其他路线可切换. 请上传新 GPX.');
        }
    } catch (e) {
        showError('删除异常: ' + e.message);
        console.error(e);
    }
}

function showError(msg) {
    document.getElementById('forecast-panel').innerHTML =
        `<div class="text-center text-red-500 py-8">${msg}</div>`;
}

// ========== Header ==========
function renderHeader(info) {
    document.getElementById('route-name').textContent = `${info.name} · ${info.start} → ${info.end}`;
    document.getElementById('route-meta').innerHTML = `
        📏 <b>${info.distance_km}</b>km &nbsp;|&nbsp;
        ⬆️ <b>${info.elevation_gain_m}</b>m &nbsp;|&nbsp;
        ⬇️ <b>${info.elevation_loss_m}</b>m &nbsp;|&nbsp;
        📊 <b>${info.min_ele_m}-${info.max_ele_m}</b>m &nbsp;|&nbsp;
        📍 共 <b>${info.total_waypoints}</b> 个标注点（关键节点 <b>${info.key_pois_count}</b>）
    `;
}

// ========== 地图 ==========
function renderMap() {
    // 守卫: Leaflet 不允许同一容器重复初始化, 切换路线前必须先 remove 旧实例
    if (map) {
        map.remove();
        map = null;
    }
    poiMarkers = {};
    const c = routeData.center;
    map = L.map('map').setView([c.lat, c.lon], 11);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenStreetMap', maxZoom: 16,
    }).addTo(map);
    L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
        attribution: '© OpenTopoMap', maxZoom: 14, opacity: 0.5,
    }).addTo(map);

    const latlngs = routeData.track.map(p => [p[1], p[0]]);
    L.polyline(latlngs, {color: '#dc2626', weight: 3, opacity: 0.8}).addTo(map);

    routeData.key_pois.forEach(poi => {
        const icon = L.divIcon({
            className: '',
            html: `<div class="poi-marker ${poi.type}" title="${poi.name}">${POI_ICON[poi.type] || '•'}</div>`,
            iconSize: [22, 22], iconAnchor: [11, 11],
        });
        const m = L.marker([poi.lat, poi.lon], {icon})
            .addTo(map);
        bindPoiPopup(m, poi);
        m.on('click', () => selectPoi(poi.id));
        poiMarkers[poi.id] = m;
    });

    routeData.waypoints.filter(w => !['camp','pass','retreat','danger'].includes(w.type)).forEach(w => {
        L.circleMarker([w.lat, w.lon], {
            radius: 3, color: '#64748b', fillColor: '#94a3b8', fillOpacity: 0.6, weight: 1,
        }).addTo(map).bindPopup(`<b>${w.name}</b><br/>${w.ele}m`);
    });

    map.fitBounds(L.latLngBounds(latlngs), {padding: [30, 30]});

    // 点击地图创建自定义标记 (仅在添加模式下生效)
    map.on('click', onMapClick);
    if (addMarkerMode) map.getContainer().classList.add('adding-mode');
}

// 给 POI marker 绑 popup: 自定义标记带删除按钮, 系统 POI 只读
function bindPoiPopup(m, poi) {
    if (poi.type === 'custom') {
        m.bindPopup(`
            <div class="wp-popup-form">
                <div class="font-medium">${poi.name}</div>
                <div class="text-xs text-slate-500 mt-1">
                    ${TYPE_LABEL.custom} · ${poi.ele}m<br/>
                    📍 ${poi.lat.toFixed(4)}, ${poi.lon.toFixed(4)}
                </div>
                <div class="actions">
                    <button class="btn-del" onclick="window.__deleteWp(${poi.id})">🗑 删除标记</button>
                </div>
            </div>
        `);
    } else {
        m.bindPopup(`<b>${poi.name}</b><br/>${TYPE_LABEL[poi.type]} · ${poi.ele}m`);
    }
}

// ========== 添加 / 删除自定义标记 ==========
function toggleAddMarkerMode() {
    if (!routeData) {
        showError('请先加载路线');
        return;
    }
    if (!window.__token) {
        showError('请先登录');
        return;
    }
    addMarkerMode ? exitAddMarkerMode() : enterAddMarkerMode();
}

function enterAddMarkerMode() {
    addMarkerMode = true;
    const btn = document.getElementById('btn-add-marker');
    btn.classList.remove('bg-amber-600', 'hover:bg-amber-500');
    btn.classList.add('bg-red-600', 'hover:bg-red-500');
    btn.innerHTML = '<span>✖ 取消添加 (ESC)</span>';
    if (map) map.getContainer().classList.add('adding-mode');
}

function exitAddMarkerMode() {
    addMarkerMode = false;
    const btn = document.getElementById('btn-add-marker');
    btn.classList.remove('bg-red-600', 'hover:bg-red-500');
    btn.classList.add('bg-amber-600', 'hover:bg-amber-500');
    btn.innerHTML = '<span>➕ 添加标记</span>';
    if (tempMarker) { tempMarker.remove(); tempMarker = null; }
    if (map) {
        map.getContainer().classList.remove('adding-mode');
        map.closePopup();
    }
}

function onMapClick(e) {
    if (!addMarkerMode) return;
    const { lat, lng } = e.latlng;
    // 默认海拔: 取轨迹最近点
    const idx = findClosestTrackIdx(lat, lng);
    const defEle = routeData.track[idx] ? routeData.track[idx][2] : 0;
    const defName = `自定义点 ${(routeData.waypoints || []).length + 1}`;
    if (tempMarker) tempMarker.remove();
    const formHtml = `
        <div class="wp-popup-form">
            <div class="font-medium">📍 创建自定义标记</div>
            <div class="text-xs text-slate-500 mt-0.5">📍 ${lat.toFixed(4)}, ${lng.toFixed(4)}</div>
            <div class="row"><label>名称</label>
                <input type="text" id="wp-name" value="${defName}"></div>
            <div class="row"><label>海拔 m</label>
                <input type="number" id="wp-ele" value="${Math.round(defEle)}" min="0" step="1"></div>
            <div class="actions">
                <button class="btn-cancel" onclick="window.__cancelAddWp()">取消</button>
                <button class="btn-ok" onclick="window.__confirmAddWp(${lat}, ${lng})">创建</button>
            </div>
        </div>`;
    tempMarker = L.marker([lat, lng], {
        icon: L.divIcon({
            className: '',
            html: '<div class="poi-marker custom">📍</div>',
            iconSize: [22, 22], iconAnchor: [11, 11],
        })
    }).addTo(map).bindPopup(formHtml).openPopup();
}

window.__confirmAddWp = async function(lat, lon) {
    const nameEl = document.getElementById('wp-name');
    const eleEl = document.getElementById('wp-ele');
    const name = (nameEl && nameEl.value || '').trim() || '自定义点';
    const ele = parseFloat(eleEl && eleEl.value) || 0;
    exitAddMarkerMode();
    await createCustomWaypoint(lat, lon, name, ele);
};

window.__cancelAddWp = function() {
    exitAddMarkerMode();
};

window.__deleteWp = async function(wpId) {
    await deleteCustomWaypoint(wpId);
};

async function createCustomWaypoint(lat, lon, name, ele) {
    const routeId = routeNameToId[routeName] || (routeData && routeData.id);
    if (!routeId) {
        showError('无法创建: 路线 ID 未知');
        return;
    }
    try {
        const r = await fetch(`/api/routes/${routeId}/waypoints`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': 'Bearer ' + window.__token,
            },
            body: JSON.stringify({ name, lat, lon, ele }),
        });
        if (r.status === 401 || r.status === 403) {
            showError('登录已过期, 请重新登录');
            return;
        }
        if (!r.ok) {
            const txt = await r.text();
            showError(`创建失败 (${r.status}): ${txt.slice(0, 200)}`);
            return;
        }
        const wp = await r.json();
        // 局部更新: routeData.key_pois / waypoints 都加上, 重新渲染地图 + 表格 + 拉预报
        const wpDict = { id: wp.id, name: wp.name, lat: wp.lat, lon: wp.lon, ele: wp.ele, type: 'custom' };
        routeData.key_pois.push(wpDict);
        routeData.waypoints.push(wpDict);
        routeData.total_waypoints = (routeData.total_waypoints || 0) + 1;
        renderMap();
        renderPoiTable();
        await loadForecast();
        selectPoi(wp.id);
    } catch (e) {
        showError('创建失败: ' + e.message);
    }
}

async function deleteCustomWaypoint(wpId) {
    if (!confirm('确定删除这个自定义标记？')) return;
    try {
        const r = await fetch(`/api/waypoints/${wpId}`, {
            method: 'DELETE',
            headers: {'Authorization': 'Bearer ' + window.__token},
        });
        if (r.status === 401 || r.status === 403) {
            showError('登录已过期, 请重新登录');
            return;
        }
        if (!r.ok) {
            const txt = await r.text();
            showError(`删除失败 (${r.status}): ${txt.slice(0, 200)}`);
            return;
        }
        // 局部更新: 从 routeData 移除
        routeData.key_pois = routeData.key_pois.filter(w => w.id !== wpId);
        routeData.waypoints = (routeData.waypoints || []).filter(w => w.id !== wpId);
        routeData.total_waypoints = Math.max(0, (routeData.total_waypoints || 0) - 1);
        delete forecastMap[wpId];
        if (selectedPoiId === wpId) selectedPoiId = null;
        renderMap();
        renderPoiTable();
        await loadForecast();
        if (!selectedPoiId && routeData.key_pois.length > 0) {
            selectPoi(routeData.key_pois[0].id);
        }
    } catch (e) {
        showError('删除失败: ' + e.message);
    }
}

// ========== 海拔剖面 ==========
function renderProfile() {
    const canvas = document.getElementById('profile-canvas');
    const ctx = canvas.getContext('2d');
    const W = canvas.width, H = canvas.height;
    const track = routeData.track;
    const dists = [0];
    for (let i = 1; i < track.length; i++) {
        dists.push(dists[i-1] + haversine(track[i-1][1], track[i-1][0], track[i][1], track[i][0]));
    }
    const totalKm = dists[dists.length - 1];
    const eles = track.map(p => p[2]);
    const minEle = Math.min(...eles), maxEle = Math.max(...eles);
    const padding = {left: 60, right: 30, top: 20, bottom: 40};
    const plotW = W - padding.left - padding.right;
    const plotH = H - padding.top - padding.bottom;

    ctx.clearRect(0, 0, W, H);
    ctx.strokeStyle = '#e2e8f0'; ctx.fillStyle = '#64748b';
    ctx.font = '11px sans-serif'; ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
        const y = padding.top + plotH * i / 5;
        const ele = maxEle - (maxEle - minEle) * i / 5;
        ctx.beginPath(); ctx.moveTo(padding.left, y); ctx.lineTo(W - padding.right, y); ctx.stroke();
        ctx.textAlign = 'right'; ctx.fillText(Math.round(ele) + 'm', padding.left - 8, y + 4);
    }
    for (let i = 0; i <= 6; i++) {
        const x = padding.left + plotW * i / 6;
        const km = totalKm * i / 6;
        ctx.textAlign = 'center'; ctx.fillText(km.toFixed(1) + 'km', x, H - padding.bottom + 18);
    }
    const xScale = plotW / totalKm, yScale = plotH / (maxEle - minEle || 1);
    ctx.beginPath();
    ctx.moveTo(padding.left, H - padding.bottom);
    for (let i = 0; i < track.length; i++) {
        ctx.lineTo(padding.left + dists[i] * xScale, padding.top + (maxEle - track[i][2]) * yScale);
    }
    ctx.lineTo(padding.left + plotW, H - padding.bottom); ctx.closePath();
    const grad = ctx.createLinearGradient(0, padding.top, 0, H - padding.bottom);
    grad.addColorStop(0, 'rgba(220, 38, 38, 0.4)'); grad.addColorStop(1, 'rgba(220, 38, 38, 0.05)');
    ctx.fillStyle = grad; ctx.fill();
    ctx.beginPath();
    for (let i = 0; i < track.length; i++) {
        const x = padding.left + dists[i] * xScale, y = padding.top + (maxEle - track[i][2]) * yScale;
        if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    }
    ctx.strokeStyle = '#dc2626'; ctx.lineWidth = 2; ctx.stroke();

    routeData.key_pois.forEach(poi => {
        const idx = findClosestTrackIdx(poi.lat, poi.lon);
        const x = padding.left + dists[idx] * xScale;
        const y = padding.top + (maxEle - track[idx][2]) * yScale;
        const colors = {camp:'#dc2626', pass:'#7c3aed', retreat:'#ea580c', danger:'#b91c1c'};
        ctx.fillStyle = colors[poi.type] || '#64748b';
        ctx.beginPath(); ctx.arc(x, y, 5, 0, Math.PI * 2); ctx.fill();
        ctx.strokeStyle = 'white'; ctx.lineWidth = 1.5; ctx.stroke();
        ctx.fillStyle = '#1e293b'; ctx.font = 'bold 10px sans-serif'; ctx.textAlign = 'center';
        const label = poi.name.length > 12 ? poi.name.slice(0, 12) + '…' : poi.name;
        ctx.fillText(label, x, y - 10);
    });
    document.getElementById('profile-meta').textContent =
        `全程 ${totalKm.toFixed(1)}km · 海拔 ${Math.round(minEle)}-${Math.round(maxEle)}m`;
}

function findClosestTrackIdx(lat, lon) {
    let best = 0, bestD = Infinity;
    for (let i = 0; i < routeData.track.length; i++) {
        const [tlon, tlat] = routeData.track[i];
        const d = (tlat - lat) ** 2 + (tlon - lon) ** 2;
        if (d < bestD) { bestD = d; best = i; }
    }
    return best;
}

function haversine(lat1, lon1, lat2, lon2) {
    const R = 6371;
    const dLat = (lat2 - lat1) * Math.PI / 180;
    const dLon = (lon2 - lon1) * Math.PI / 180;
    const a = Math.sin(dLat/2) ** 2 +
        Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
        Math.sin(dLon/2) ** 2;
    return 2 * R * Math.asin(Math.sqrt(a));
}

// ========== POI 表 ==========
function renderPoiTableHeader() {
    const row = document.getElementById('poi-table-header-row');
    if (!row) return;
    // 保留前 4 列 (#, 节点, 类型, 海拔), 末列 (数据源)
    const prefix = `
        <th class="px-3 py-2 text-left">#</th>
        <th class="px-3 py-2 text-left">节点</th>
        <th class="px-3 py-2 text-left">类型</th>
        <th class="px-3 py-2 text-right">海拔</th>`;
    const dayHeaders = Array.from({length: tripDays}, (_, i) =>
        `<th class="px-3 py-2 text-center">D${i + 1}</th>`
    ).join('');
    row.innerHTML = prefix + dayHeaders + `<th class="px-3 py-2 text-left">数据源</th>`;
}

function renderPoiTable() {
    const tbody = document.getElementById('poi-table-body');
    tbody.innerHTML = '';
    if (!routeData || !routeData.key_pois) return;
    routeData.key_pois.forEach(poi => {
        const tr = document.createElement('tr');
        tr.className = 'poi-row';
        tr.dataset.poiId = poi.id;
        tr.onclick = () => selectPoi(poi.id);
        const dayCells = Array.from({length: tripDays}, (_, i) =>
            `<td class="px-3 py-2"><div class="badges-cell" id="d${i + 1}-${poi.id}">…</div></td>`
        ).join('');
        tr.innerHTML = `
            <td class="px-3 py-2 text-slate-500">${poi.id}</td>
            <td class="px-3 py-2 font-medium">${poi.name}</td>
            <td class="px-3 py-2"><span class="badge ${typeBadgeClass(poi.type)}">${TYPE_LABEL[poi.type]}</span></td>
            <td class="px-3 py-2 text-right">${poi.ele}m</td>
            ${dayCells}
            <td class="px-3 py-2 text-xs text-slate-500" id="sources-${poi.id}">…</td>
        `;
        tbody.appendChild(tr);
    });
}

function typeBadgeClass(type) {
    return {camp:'badge-red', pass:'badge-blue', retreat:'badge-yellow', danger:'badge-red', water:'badge-blue', custom:'badge-yellow'}[type] || 'badge-gray';
}

// ========== 预报拉取 ==========
async function loadForecast() {
    try {
        const r = await fetch(`/api/forecast?route_name=${encodeURIComponent(routeName)}`);
        const data = await r.json();
        if (!r.ok) {
            const detail = (data && data.detail) ? data.detail : `HTTP ${r.status}`;
            showError(`预报拉取失败 (${r.status}): ${detail}`);
            return;
        }
        updateSourceBadges(data.sources_available);
        if (!data.points || data.points.length === 0) {
            showError('该路线没有关键节点 (camp/pass/retreat/danger), 无法生成预报。'
                   + '请检查 GPX 是否包含带"营地/垭口/下撤/危险"关键字的 waypoint。');
            return;
        }
        data.points.forEach(item => {
            forecastMap[item.poi.id] = item;
            renderPoiDayBadges(item);
        });
        // 上传/切换后自动展示第一个 POI 的预报
        // 否则 #forecast-panel 会卡在上一次的 showError('上传中') 文案
        const firstPoi = (routeData.key_pois && routeData.key_pois[0]) || null;
        if (firstPoi && forecastMap[firstPoi.id]) {
            selectPoi(firstPoi.id);
        }
        rerenderForDeparture();
    } catch (e) {
        showError('预报拉取失败: ' + e.message);
    }
}

function updateSourceBadges(avail) {
    if (!avail) return;
    const set = (id, label, on) => {
        const el = document.getElementById(id);
        if (!el) return;
        el.textContent = on ? label : label + ' 未配置';
        el.className = 'badge ' + (on ? 'badge-blue' : 'badge-gray');
    };
    // 后端返回的键名是 'open-meteo' (连字符), 'qweather', 'caiyun'. 兼容两种写法.
    const om = avail['open-meteo'] ?? avail.open_meteo;
    set('src-om', 'Open-Meteo', om);
    set('src-qw', '和风天气', avail.qweather);
    set('src-cy', '彩云天气', avail.caiyun);
}

function renderPoiDayBadges(item) {
    const poiId = item.poi.id;
    const dayDates = getTripDates(tripDays);
    for (let i = 0; i < tripDays; i++) {
        const k = 'd' + (i + 1);
        const dayData = (item.daily || []).find(d => d.date === dayDates[i]);
        const cell = document.getElementById(`${k}-${poiId}`);
        if (!cell) continue;
        if (!dayData) {
            cell.innerHTML = '<span class="text-xs text-slate-400">-</span>';
            continue;
        }
        const b = dayData.badges;
        const glowHtml = dayData.glow
            ? `<div class="mt-0.5">${glowMini(dayData.glow)}</div>`
            : '';
        cell.innerHTML = `<div class="badges-cell">${badgeMini(b.hypothermia,'失')}${badgeMini(b.thunderstorm,'雷')}${badgeMini(b.altitude,'高')}</div>${glowHtml}`;
    }
    const srcCell = document.getElementById(`sources-${poiId}`);
    if (srcCell) srcCell.textContent = item.sources.join('+') + (item._from_cache ? '·缓存' : '');
}

function badgeMini(level, label) {
    const cls = level === 'red' ? 'red' : level === 'yellow' ? 'yellow' : level === 'green' ? 'green' : 'low';
    return `<span class="badge-mini ${cls}">${label}</span>`;
}

// ========== 晚霞徽章 ==========
const GLOW_LEVEL_LABEL = {
    red: '强烈推荐', yellow: '值得等待', green: '可观望', low: '不推荐',
};
const GLOW_LEVEL_DESC = {
    red: '史诗级霞光，火烧天级别，值得专程守候',
    yellow: '不错的霞光，值得专门等待十几分钟',
    green: '平淡霞光，天边可能泛红但不强烈',
    low: '基本看不到，被云/雾/降水遮蔽',
};
const GLOW_SCORE_DESC = (s) => {
    if (s >= 6) return '极佳';
    if (s >= 3) return '不错';
    if (s >= 1) return '一般';
    return '差';
};

function glowMini(glow) {
    // 速览表用 (18×18 圆点 + hover/tap tooltip)
    if (!glow || !glow.available) {
        const cls = 'low';
        return `<span class="badge-glow-wrap" tabindex="0">
            <span class="badge-glow-mini ${cls}">霞</span>
            <span class="badge-glow-tooltip"><b>${GLOW_LEVEL_LABEL[cls]}</b><br/><span class="text-slate-400">${glow ? glow.reason || '日落数据缺失' : '无数据'}</span></span>
        </span>`;
    }
    const cls = ['red', 'yellow', 'green'].includes(glow.level) ? glow.level : 'low';
    const sunsetShort = glow.sunset ? glow.sunset.slice(11, 16) : '?';
    const bestShort = glow.best_time ? glow.best_time.slice(11, 16) : '?';
    return `<span class="badge-glow-wrap" tabindex="0">
        <span class="badge-glow-mini ${cls}">霞</span>
        <span class="badge-glow-tooltip">
            <b>${GLOW_LEVEL_LABEL[cls]}</b> · ${GLOW_SCORE_DESC(glow.score)}<br/>
            <span class="text-slate-300">${GLOW_LEVEL_DESC[cls]}</span>
            <hr/>
            <span>日落 <b>${sunsetShort}</b> · 最佳 <b>${bestShort}</b></span><br/>
            <span>评分 <b>${glow.score}/8</b></span>
            <hr/>
            <span>具体因素：${glow.reason || '无显著因素'}</span>
        </span>
    </span>`;
}

function glowBadge(glow) {
    // 详情面板 daily 表用 (椭圆 + hover/tap 显示白话 tooltip)
    if (!glow || !glow.available) {
        return `<span class="badge-glow-wrap" tabindex="0">
            <span class="badge-glow low">🌅 —</span>
            <span class="badge-glow-tooltip"><b>${GLOW_LEVEL_LABEL.low}</b> — ${GLOW_LEVEL_DESC.low}<br/><span class="text-slate-400">${glow ? glow.reason || '日落数据缺失' : '无数据'}</span></span>
        </span>`;
    }
    const cls = ['red', 'yellow', 'green'].includes(glow.level) ? glow.level : 'low';
    const sunsetShort = glow.sunset ? glow.sunset.slice(11, 16) : '?';
    const bestShort = glow.best_time ? glow.best_time.slice(11, 16) : '?';
    const reason = glow.reason || '无显著因素';
    return `<span class="badge-glow-wrap" tabindex="0">
        <span class="badge-glow ${cls}">🌅${glow.score} ${sunsetShort}</span>
        <span class="badge-glow-tooltip">
            <b>${GLOW_LEVEL_LABEL[cls]}</b> · ${GLOW_SCORE_DESC(glow.score)}<br/>
            <span class="text-slate-300">${GLOW_LEVEL_DESC[cls]}</span>
            <hr/>
            <span>日落 <b>${sunsetShort}</b> · 最佳 <b>${bestShort}</b></span><br/>
            <span>评分 <b>${glow.score}/8</b></span>
            <hr/>
            <span>具体因素：${reason}</span>
        </span>
    </span>`;
}

// ========== 装备建议 ==========
async function loadGear() {
    try {
        // 把当前选定行程日期传给后端, 避免"今天 2mm 但装备说 7 天最大 22mm"的错配.
        // 没有 departureDate 时退化为 7 天全程 (后端兜底).
        const params = new URLSearchParams({route_name: routeName});
        if (departureDate) {
            params.set('date', departureDate);
            params.set('days', String(tripDays));
        }
        const r = await fetch(`/api/gear?${params}`);
        gearData = await r.json();
        renderGear();
    } catch (e) {
        document.getElementById('gear-body').innerHTML = `<div class="text-center text-red-500 col-span-3 py-6">装备建议拉取失败: ${e.message}</div>`;
    }
}

function renderGear() {
    if (!gearData) return;
    const scope = gearData.scope || '';
    const feat = gearData.route_feature || '';
    const metaHtml = (scope || feat) ? `
        <div class="col-span-3 mb-1 text-xs text-slate-600 bg-slate-50 border border-slate-200 rounded px-3 py-2 flex flex-wrap gap-x-4 gap-y-1">
            ${scope ? `<span>📅 <b>评估范围:</b> ${scope}</span>` : ''}
            ${feat ? `<span>⛰️ <b>路线特征:</b> ${feat}</span>` : ''}
        </div>
    ` : '';
    const html = `
        ${metaHtml}
        <div class="border border-red-200 rounded-lg p-3 bg-red-50">
            <div class="font-semibold text-red-700 mb-2 flex items-center gap-2"><span class="bg-red-600 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-xs">必</span>必带 (${gearData.must.length})</div>
            <ul class="space-y-1 text-sm">
                ${gearData.must.map(g => `<li><b>${g[0]}</b> <span class="text-xs text-slate-500">— ${g[1]}</span></li>`).join('')}
            </ul>
        </div>
        <div class="border border-yellow-200 rounded-lg p-3 bg-yellow-50">
            <div class="font-semibold text-yellow-700 mb-2 flex items-center gap-2"><span class="bg-yellow-500 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-xs">议</span>建议 (${gearData.suggest.length})</div>
            <ul class="space-y-1 text-sm">
                ${gearData.suggest.map(g => `<li><b>${g[0]}</b> <span class="text-xs text-slate-500">— ${g[1]}</span></li>`).join('')}
            </ul>
        </div>
        <div class="border border-slate-200 rounded-lg p-3 bg-slate-50">
            <div class="font-semibold text-slate-600 mb-2 flex items-center gap-2"><span class="bg-slate-400 text-white rounded-full w-6 h-6 inline-flex items-center justify-center text-xs">无</span>不必 (${gearData.skip.length})</div>
            <ul class="space-y-1 text-sm">
                ${gearData.skip.map(g => `<li><b>${g[0]}</b> <span class="text-xs text-slate-500">— ${g[1]}</span></li>`).join('')}
            </ul>
        </div>
    `;
    document.getElementById('gear-body').innerHTML = html;
}

// ========== 逐分钟降水 (Open-Meteo minutely_15) ==========
async function loadMinutely(lat, lon) {
    const el = document.getElementById('minutely-section');
    if (!el) return;
    el.innerHTML = '<div class="text-xs text-slate-400">⏳ 加载逐分钟降水...</div>';
    try {
        const r = await fetch(`/api/minutely?lat=${lat}&lon=${lon}&hours=2`);
        const d = await r.json();
        renderMinutely(d);
    } catch (e) {
        el.innerHTML = `<div class="text-xs text-red-500">❌ 逐分钟降水拉取失败: ${e.message}</div>`;
    }
}

function renderMinutely(data) {
    const el = document.getElementById('minutely-section');
    if (!el) return;
    if (data.error) {
        el.innerHTML = `<div class="text-xs text-red-500">⚠️ ${data.error}</div>`;
        return;
    }
    const arr = data.precip_mm || [];
    if (arr.length === 0) {
        el.innerHTML = '<div class="text-xs text-slate-400">无数据</div>';
        return;
    }
    const maxP = Math.max(...arr, 0.1);
    const bars = arr.map((p, i) => {
        const h = Math.max(4, (p / maxP) * 100);
        let color = '#e2e8f0';
        if (p >= 5) color = '#1e3a8a';
        else if (p >= 1) color = '#2563eb';
        else if (p >= 0.1) color = '#93c5fd';
        return `<div class="flex-1 rounded-t" style="height:${h}%;background:${color};min-height:4px" title="${data.times[i]}: ${p}mm (概率 ${data.precip_prob[i]}%)"></div>`;
    }).join('');
    const mid = Math.floor(arr.length / 2);
    el.innerHTML = `
        <div class="text-sm font-semibold mb-1">⏱️ 未来 ${arr.length / 4} 小时降水 <span class="text-xs font-normal text-slate-500">(15 分钟精度)</span></div>
        <div class="text-xs text-slate-600 mb-2">${data.summary || ''}</div>
        <div class="flex items-end gap-1 h-12">${bars}</div>
        <div class="flex justify-between text-xs text-slate-400 mt-1">
            <span>${data.times[0]}</span>
            <span>${data.times[mid]}</span>
            <span>${data.times[arr.length - 1]}</span>
        </div>
        <div class="text-xs text-slate-400 mt-1">数据源: ${data.source} · 拉取于 ${data.fetched_at || '?'} · ${data.note || ''}</div>
    `;
}

// ========== 恶劣天气预警 (QWeather JWT) ==========
async function loadAlerts(lat, lon) {
    const el = document.getElementById('alerts-section');
    if (!el) return;
    el.innerHTML = '<div class="text-xs text-slate-400 px-4 py-2">⏳ 查询气象预警...</div>';
    try {
        const r = await fetch(`/api/alerts?lat=${lat}&lon=${lon}`);
        const d = await r.json();
        renderAlerts(d);
    } catch (e) {
        el.innerHTML = `<div class="text-xs text-red-500 px-4 py-2">❌ 预警拉取失败: ${e.message}</div>`;
    }
}

function renderAlerts(data) {
    const el = document.getElementById('alerts-section');
    if (!el) return;
    if (data.error && (!data.alerts || data.alerts.length === 0)) {
        el.innerHTML = `<div class="text-xs text-slate-400 px-4 py-2">⚠️ 预警查询失败: ${data.error}</div>`;
        return;
    }
    if (!data.alerts || data.alerts.length === 0) {
        el.innerHTML = `<div class="text-xs text-slate-400 px-4 py-2">✅ 当前无活跃气象预警 · ${data.fetched_at || ''}</div>`;
        return;
    }
    const colorMap = {
        blue:   {bg: '#dbeafe', fg: '#1e40af', label: '蓝色'},
        yellow: {bg: '#fef3c7', fg: '#854d0e', label: '黄色'},
        orange: {bg: '#ffedd5', fg: '#9a3412', label: '橙色'},
        red:    {bg: '#fee2e2', fg: '#991b1b', label: '红色'},
    };
    const cards = data.alerts.map(a => {
        const c = colorMap[a.level] || colorMap.yellow;
        return `
            <div class="px-4 py-3 border-b" style="background:${c.bg};color:${c.fg}">
                <div class="font-bold text-sm">⚠️ ${a.title}</div>
                ${a.description ? `<div class="text-xs mt-1">${a.description}</div>` : ''}
                ${a.instruction ? `<div class="text-xs mt-1 italic">防御指南: ${a.instruction}</div>` : ''}
                <div class="text-xs mt-1 opacity-75">有效期: ${a.effective || '?'} → ${a.expire || '?'}</div>
            </div>
        `;
    }).join('');
    el.innerHTML = cards + `<div class="text-xs text-slate-400 px-4 py-1 border-t">数据源: ${data.source} · 拉取于 ${data.fetched_at || '?'}</div>`;
}

// ========== 轨迹分段天气预估 (后端算) ==========
let trackSpeed = 3.0;

async function loadTrackWeather(name, departure, speed) {
    const el = document.getElementById('track-segment-section');
    if (!el) return;
    el.innerHTML = '<div class="text-xs text-slate-400 py-2">⏳ 正在计算分段天气 (并发拉每段预报)...</div>';
    try {
        const params = new URLSearchParams({route_name: name, speed: String(speed)});
        if (departure) params.set('departure', departure);
        const r = await fetch(`/api/track_weather?${params}`);
        const d = await r.json();
        renderTrackSegments(d);
    } catch (e) {
        el.innerHTML = `<div class="text-xs text-red-500 py-2">❌ 分段天气拉取失败: ${e.message}</div>`;
    }
}

function renderTrackSegments(data) {
    const el = document.getElementById('track-segment-section');
    if (!el) return;
    if (data.error) {
        el.innerHTML = `<div class="text-xs text-red-500 py-2">❌ ${data.error}</div>`;
        return;
    }
    const segs = data.segments || [];
    // 出发日距今 > 7 天 → 长波单源, 精度低
    const departureDayOffset = departureDate
        ? Math.floor((new Date(departureDate + 'T00:00:00+08:00') - new Date(new Date().toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}) + 'T00:00:00+08:00')) / 86400000)
        : 0;
    const isLongRangeTrip = departureDayOffset >= 7;
    const rows = segs.map(s => {
        const thunderClass = s.has_thunder ? 'bg-red-50' : '';
        let cell;
        if (s.error) {
            cell = `<span class="text-red-500">${s.error}</span>`;
        } else {
            cell = `${s.weather_label || '?'} · <b>${s.temp_real}°</b>/${s.temp_wct}° · ${s.precip_mm}mm · ${s.wind_kmh}/${s.gust_kmh}km/h${s.has_thunder ? ' ⚡' : ''}`;
        }
        return `<tr class="${thunderClass}">
            <td>${s.index}</td>
            <td>${s.cum_distance_km}</td>
            <td>${s.ele_m}m</td>
            <td><b>${(s.arrival_time || '').slice(11, 16)}</b></td>
            <td>${cell}</td>
        </tr>`;
    }).join('');
    el.innerHTML = `
        <div class="flex items-center justify-between mb-2">
            <div class="text-sm font-semibold">🥾 轨迹分段天气预估${isLongRangeTrip ? ' <span class="text-amber-700 text-xs">(长期参考 · 精度低)</span>' : ''}</div>
            <div class="flex items-center gap-2 text-xs">
                <span>速度</span>
                <input type="range" id="track-speed-slider" min="1.5" max="6" step="0.5" value="${data.estimated_speed_kmh}" class="w-24"/>
                <span id="track-speed-label">${data.estimated_speed_kmh} km/h</span>
            </div>
        </div>
        <div class="text-xs text-slate-600 mb-2">总距离 ${data.total_distance_km}km · 估时 ${data.estimated_total_hours}h · 出发 ${data.departure}${isLongRangeTrip ? ' · <span class="text-amber-700">⚠ 出发日距今天 ' + departureDayOffset + ' 天, 超 7 天部分为 OM 长波单源</span>' : ''}</div>
        <table class="w-full text-xs">
            <thead><tr><th class="text-left">段</th><th class="text-left">km</th><th class="text-left">海拔</th><th class="text-left">到达</th><th class="text-left">天气/温度/降水/风</th></tr></thead>
            <tbody>${rows}</tbody>
        </table>
        <div class="text-xs text-slate-400 mt-1">数据源: ${data.source} · 拉取于 ${data.fetched_at || '?'} · ${data.note || ''}</div>
    `;
    const slider = document.getElementById('track-speed-slider');
    if (slider) {
        slider.addEventListener('input', (e) => {
            trackSpeed = parseFloat(e.target.value);
            const lbl = document.getElementById('track-speed-label');
            if (lbl) lbl.textContent = `${trackSpeed.toFixed(1)} km/h`;
        });
        slider.addEventListener('change', () => {
            if (routeName) loadTrackWeather(routeName, departureDate, trackSpeed);
        });
    }
}

// ========== 行程日历 + 整体风险 ==========
function getTripDates(numDays) {
    if (!departureDate) return [];
    const start = new Date(departureDate + 'T00:00:00+08:00');
    return Array.from({length: numDays}, (_, i) => {
        const d = new Date(start);
        d.setDate(d.getDate() + i);
        return d.toLocaleDateString('sv-SE', {timeZone: 'Asia/Shanghai'});
    });
}

function levelSeverity(l) { return {low:0, green:1, yellow:2, red:3}[l] || 0; }

function rerenderForDeparture() {
    renderTripRiskBanner();
    // 更新所有 POI 的 D1-D{N} 徽章 (基于新日期与天数)
    Object.values(forecastMap).forEach(item => renderPoiDayBadges(item));
    // 更新选中节点的预报面板
    if (selectedPoiId) renderForecastPanel(forecastMap[selectedPoiId]);
    // 装备建议也跟着重新拉, 因为只看选定天数的天气
    loadGear();
    // 轨迹分段天气也跟着重新拉, 因为到达时刻依赖出发日
    if (routeName) loadTrackWeather(routeName, departureDate, trackSpeed);
}

function renderTripRiskBanner() {
    const dayDates = getTripDates(tripDays);
    const body = document.getElementById('trip-risk-body');
    const meta = document.getElementById('trip-risk-meta');
    const rangeEl = document.getElementById('trip-risk-days-range');
    if (rangeEl) rangeEl.textContent = `(D1-D${tripDays})`;

    if (dayDates.length === 0 || Object.keys(forecastMap).length === 0) {
        body.innerHTML = '<div class="text-center text-slate-400 py-4">⏳ 加载中...</div>';
        return;
    }

    meta.textContent = `出发日 ${departureDate} · 行程 ${dayDates[0]} → ${dayDates[dayDates.length - 1]}`;

    // 计算每天最严重的徽章 (跨所有 POI)
    const dayRisks = dayDates.map((date, i) => {
        const worst = {hypothermia: 'green', thunderstorm: 'green', altitude: 'low'};
        Object.values(forecastMap).forEach(item => {
            const dayData = (item.daily || []).find(d => d.date === date);
            if (!dayData) return;
            ['hypothermia','thunderstorm'].forEach(k => {
                if (levelSeverity(dayData.badges[k]) > levelSeverity(worst[k])) {
                    worst[k] = dayData.badges[k];
                }
            });
        });
        return {date, idx: i+1, ...worst};
    });

    // 整体最严重
    const overall = {hypothermia: 'green', thunderstorm: 'green'};
    dayRisks.forEach(d => {
        ['hypothermia','thunderstorm'].forEach(k => {
            if (levelSeverity(d[k]) > levelSeverity(overall[k])) overall[k] = d[k];
        });
    });
    const overallColor = (overall.hypothermia === 'red' || overall.thunderstorm === 'red') ? 'red'
                       : (overall.hypothermia === 'yellow' || overall.thunderstorm === 'yellow') ? 'yellow' : 'green';
    const recText = overallColor === 'red' ? '高风险 · 建议改期或加强装备'
                  : overallColor === 'yellow' ? '警惕 · 装备补足后可出发'
                  : '注意 · 标准装备可出发';

    const bannerBg = {red:'#fee2e2', yellow:'#fef3c7', green:'#dcfce7'}[overallColor];
    const bannerBorder = {red:'#dc2626', yellow:'#eab308', green:'#16a34a'}[overallColor];
    const bannerText = {red:'#991b1b', yellow:'#854d0e', green:'#166534'}[overallColor];

    body.innerHTML = `
        <div class="rounded-lg p-4 mb-3" style="background:${bannerBg}; border:2px solid ${bannerBorder};">
            <div class="text-lg font-bold" style="color:${bannerText};">
                ${overallColor === 'red' ? '🚨' : overallColor === 'yellow' ? '⚠️' : '✅'} 整体评估: ${recText}
            </div>
            <div class="text-sm mt-1" style="color:${bannerText}; opacity:0.85;">
                失温风险: <b>${zh(overall.hypothermia)}</b> · 雷暴风险: <b>${zh(overall.thunderstorm)}</b>
            </div>
        </div>
        <div class="grid grid-cols-4 gap-3">
            ${dayRisks.map(d => `
                <div class="border rounded-lg p-3 ${d.idx === 1 ? 'border-amber-400 bg-amber-50' : 'border-slate-200 bg-slate-50'}">
                    <div class="text-xs text-slate-500">${weekday(d.date)}</div>
                    <div class="font-bold text-slate-800">D${d.idx} · ${d.date.slice(5)}</div>
                    <div class="flex gap-1 mt-2 justify-center">
                        ${badgeMini(d.hypothermia, '失温')}
                        ${badgeMini(d.thunderstorm, '雷暴')}
                    </div>
                </div>
            `).join('')}
        </div>
    `;
}

function zh(level) {
    return {red:'红警', yellow:'警惕', green:'注意', low:'低'}[level] || level;
}

// ========== 择日对比 (跨所有 POI 取最严徽章/极端值, 不需要后端新端点) ==========

function computeDayRisk(dateStr) {
    const allItems = Object.values(forecastMap);
    if (allItems.length === 0) return null;
    const worst = {hypothermia: 'green', thunderstorm: 'green'};
    let isLongRange = false, hasData = false, hasThunder = false;
    let tmin_min = Infinity, wct_min = Infinity;
    let precip_max = 0, precip_prob_max = 0, gust_max = 0;
    let glowSum = 0, glowCount = 0;
    const sources = new Set();
    allItems.forEach(item => {
        const dayData = (item.daily || []).find(d => d.date === dateStr);
        if (!dayData) return;
        hasData = true;
        if (dayData.is_long_range) isLongRange = true;
        ['hypothermia','thunderstorm'].forEach(k => {
            if (levelSeverity(dayData.badges[k]) > levelSeverity(worst[k])) {
                worst[k] = dayData.badges[k];
            }
        });
        tmin_min = Math.min(tmin_min, dayData.tmin_real);
        wct_min = Math.min(wct_min, dayData.wct_night);
        precip_max = Math.max(precip_max, dayData.precip_mm || 0);
        precip_prob_max = Math.max(precip_prob_max, dayData.precip_prob || 0);
        gust_max = Math.max(gust_max, dayData.gust_max || 0);
        if (dayData.has_thunder) hasThunder = true;
        if (dayData.glow && dayData.glow.available && typeof dayData.glow.score === 'number') {
            glowSum += dayData.glow.score;
            glowCount++;
        }
        (dayData.sources || []).forEach(s => sources.add(s));
    });
    if (!hasData) return null;
    const glowAvg = glowCount > 0 ? glowSum / glowCount : null;
    return {
        date: dateStr,
        weekday: weekday(dateStr),
        is_long_range: isLongRange,
        glow_avg: glowAvg !== null ? Math.round(glowAvg * 10) / 10 : null,
        glow_level: glowAvg !== null ? (glowAvg >= 5 ? 'green' : glowAvg >= 2.5 ? 'yellow' : 'red') : 'low',
        tmin_min: isFinite(tmin_min) ? Math.round(tmin_min) : null,
        wct_min: isFinite(wct_min) ? Math.round(wct_min) : null,
        precip_max: Math.round(precip_max * 10) / 10,
        precip_prob_max,
        gust_max: Math.round(gust_max),
        has_thunder: hasThunder,
        hypothermia: worst.hypothermia,
        thunderstorm: worst.thunderstorm,
        sources: Array.from(sources),
    };
}

function scoreDay(d) {
    if (!d) return {score: -Infinity, reasons: []};
    let score = 0;
    const reasons = [];
    if (d.glow_avg !== null) {
        score += d.glow_avg;
        if (d.glow_avg >= 5) reasons.push(`glow ${d.glow_avg} 分较高`);
        else if (d.glow_avg < 2.5) reasons.push(`glow 仅 ${d.glow_avg} 分`);
    }
    if (d.thunderstorm === 'red') { score -= 6; reasons.push('雷暴红警'); }
    else if (d.thunderstorm === 'yellow') { score -= 3; reasons.push('雷暴警惕'); }
    if (d.hypothermia === 'red') { score -= 5; reasons.push('失温红警'); }
    else if (d.hypothermia === 'yellow') { score -= 2; reasons.push('失温警惕'); }
    if (d.precip_max >= 15) { score -= 3; reasons.push(`降水 ${d.precip_max}mm 较大`); }
    else if (d.precip_max >= 5) { score -= 1; }
    if (d.is_long_range) reasons.push('长期参考精度低');
    return {score, reasons};
}

function getDaysFromToday(dateStr) {
    const today = new Date(new Date().toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}) + 'T00:00:00+08:00');
    const d = new Date(dateStr + 'T00:00:00+08:00');
    return Math.round((d - today) / 86400000);
}

function getNext3Saturdays() {
    const today = new Date(new Date().toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}) + 'T00:00:00+08:00');
    const dow = today.getDay();
    const daysToSat = ((6 - dow) % 7) || 7;  // 下个周六 (不含今天, 至少 1 天后)
    const out = [];
    for (let i = 0; i < 3; i++) {
        const d = new Date(today.getTime() + (daysToSat + i * 7) * 86400000);
        out.push(d.toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}));
    }
    return out;
}

function renderCompareIntro(savedDates) {
    const defaults = (savedDates && savedDates.length === 3) ? savedDates : getNext3Saturdays();
    return `
        <div class="bg-slate-100 rounded-lg p-3 mb-3">
            <div class="text-sm font-semibold mb-2 text-slate-700">选择 2-3 个候选出发日 (默认下 3 个周六):</div>
            <div class="grid grid-cols-3 gap-2">
                ${[0,1,2].map(i => `
                    <div>
                        <label class="text-xs text-slate-500">候选 ${i+1}</label>
                        <input type="date" class="compare-date w-full mt-1 px-2 py-1.5 rounded border border-slate-300 text-sm" value="${defaults[i] || ''}">
                    </div>
                `).join('')}
            </div>
            <button id="btn-compare-run" class="mt-3 bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-1.5 rounded text-sm font-medium">▶ 开始对比</button>
        </div>
    `;
}

function renderRecommendBanner(best) {
    const d = best.risk;
    const reasonText = best.scored.reasons.length > 0 ? best.scored.reasons.join(' · ') : '综合评分最优';
    const dayOffset = getDaysFromToday(d.date);
    const longRangeWarn = d.is_long_range
        ? `<div class="text-xs mt-1 text-amber-700">⚠ 该日为 ${dayOffset} 天后, 长期参考 · 实际可能变化, 出发前请复核</div>` : '';
    return `
        <div class="rounded-lg p-3 mb-3" style="background:#dcfce7; border:2px solid #16a34a;">
            <div class="text-base font-bold text-green-800">🟢 推荐 ${d.date} (${d.weekday})</div>
            <div class="text-sm mt-1 text-green-700">理由: ${reasonText}</div>
            ${longRangeWarn}
        </div>
    `;
}

function renderCompareCard(d) {
    if (!d.risk) {
        return `
            <div class="border border-slate-200 rounded-lg p-3 bg-red-50">
                <div class="font-bold text-slate-800">${d.date}</div>
                <div class="text-xs text-red-600 mt-2">❌ 该日无预报数据 (超 16 天预报范围)</div>
            </div>
        `;
    }
    const r = d.risk;
    const dayOffset = getDaysFromToday(r.date);
    const longRangeBanner = r.is_long_range
        ? `<div class="text-xs px-2 py-1 mb-2 rounded bg-amber-50 border border-amber-200 text-amber-700">⚠ 长期参考 · ${dayOffset} 天后 · 精度低</div>` : '';
    const glowCls = r.is_long_range ? 'low low-confidence' : r.glow_level;
    const glowHtml = r.glow_avg !== null
        ? `<span class="badge-glow-mini ${glowCls}">霞 ${r.glow_avg}</span>`
        : '<span class="badge-glow-mini low">霞 —</span>';
    return `
        <div class="border ${r.is_long_range ? 'border-amber-300' : 'border-slate-200'} rounded-lg p-3 bg-white">
            ${longRangeBanner}
            <div class="text-xs text-slate-500">${r.weekday}</div>
            <div class="font-bold text-slate-800">${r.date}</div>
            <div class="flex gap-1 mt-2 flex-wrap justify-center items-center">
                ${badgeMini(r.hypothermia, '失')}
                ${badgeMini(r.thunderstorm, '雷')}
                ${glowHtml}
            </div>
            <div class="grid grid-cols-2 gap-1 mt-3 text-xs text-slate-600">
                <div>🌡 最低 <b>${r.tmin_min !== null ? r.tmin_min + '°' : '?'}</b></div>
                <div>❄️ 体感 <b>${r.wct_min !== null ? r.wct_min + '°' : '?'}</b></div>
                <div>🌧 降水 <b>${r.precip_max}mm</b></div>
                <div>💧 概率 <b>${r.precip_prob_max}%</b></div>
                <div>💨 阵风 <b>${r.gust_max}</b></div>
                <div>${r.has_thunder ? '⚡ 有雷暴' : '☀ 无雷暴'}</div>
            </div>
        </div>
    `;
}

function renderComparePanel(savedDates) {
    const body = document.getElementById('compare-body');
    if (!body) return;
    const inputs = body.querySelectorAll('input.compare-date');
    const dates = inputs.length > 0
        ? Array.from(inputs).map(i => i.value).filter(Boolean)
        : (savedDates || []);
    if (dates.length === 0) {
        body.innerHTML = renderCompareIntro([]);
        return;
    }
    const days = dates.map(dt => ({date: dt, risk: computeDayRisk(dt), scored: null}));
    days.forEach(d => { d.scored = scoreDay(d.risk); });
    const validDays = days.filter(d => d.risk);
    let best = null;
    if (validDays.length > 0) {
        best = validDays.reduce((a, b) => a.scored.score >= b.scored.score ? a : b);
    }
    body.innerHTML = `
        ${renderCompareIntro(dates)}
        ${best ? renderRecommendBanner(best) : ''}
        <div class="grid grid-cols-${Math.min(3, dates.length)} gap-3 mt-3">
            ${days.map(d => renderCompareCard(d)).join('')}
        </div>
        <div class="text-xs text-slate-500 mt-3">
            说明: 跨所有 POI 取最严徽章 + 极端值; glow 为 POI 平均分; 长期日卡片黄边 + glow 灰显表示精度低
        </div>
    `;
}

function bindCompareButton() {
    const btn = document.getElementById('btn-compare');
    const btnClose = document.getElementById('btn-compare-close');
    const section = document.getElementById('compare-section');
    const body = document.getElementById('compare-body');
    if (!btn || !section || !body) return;
    btn.addEventListener('click', () => {
        section.classList.toggle('hidden');
        if (!section.classList.contains('hidden')) {
            if (!body.innerHTML.trim()) body.innerHTML = renderCompareIntro([]);
            section.scrollIntoView({behavior:'smooth', block:'start'});
        }
    });
    if (btnClose) {
        btnClose.addEventListener('click', () => section.classList.add('hidden'));
    }
    body.addEventListener('click', e => {
        if (e.target && e.target.id === 'btn-compare-run') renderComparePanel();
    });
}

// ========== 选中节点 ==========
function selectPoi(poiId) {
    selectedPoiId = poiId;
    document.querySelectorAll('.poi-row').forEach(r => {
        r.classList.toggle('selected', parseInt(r.dataset.poiId) === poiId);
    });
    if (poiMarkers[poiId]) {
        const ll = poiMarkers[poiId].getLatLng();
        map.flyTo([ll.lat, ll.lng], 12, {duration: 0.5});
    }
    renderForecastPanel(forecastMap[poiId]);
    // 逐分钟降水 + 气象预警: 跟随 POI 点位拉取 (与该点 1km 精度匹配)
    const poi = forecastMap[poiId]?.poi;
    if (poi) {
        loadMinutely(poi.lat, poi.lon);
        loadAlerts(poi.lat, poi.lon);
    }
}

function renderForecastPanel(item) {
    if (!item) {
        document.getElementById('forecast-panel').innerHTML = '<div class="text-center text-slate-400 py-8">⏳ 预报加载中...</div>';
        return;
    }
    if (item.error) {
        document.getElementById('forecast-panel').innerHTML = `<div class="text-center text-red-500 py-8">❌ ${item.error}</div>`;
        return;
    }
    const poi = item.poi;
    const tripDates = getTripDates(tripDays);
    if (!selectedHourlyDate || !tripDates.includes(selectedHourlyDate)) {
        selectedHourlyDate = tripDates[0] || (item.daily[0] && item.daily[0].date) || '';
    }
    const todayBadges = (item.daily.find(d => d.date === tripDates[0]) || item.daily[0]).badges;

    // 选中日是否处于 8-16 天长期段 → hourly 子表加红色警示条
    const selDay = item.daily.find(d => d.date === selectedHourlyDate);
    const selDayLongRange = !!(selDay && selDay.is_long_range);

    // 海拔修正标注
    const hDiff = item.h_grid ? (poi.ele - item.h_grid) : 0;  // 实际 - 网格
    const tDiff = (6 * hDiff / 1000);  // 温差 (°C)
    const hDiffLabel = Math.abs(hDiff) < 30
        ? '<span class="text-emerald-400">网格与实际海拔接近, 修正微小</span>'
        : hDiff > 0
            ? `<span class="text-amber-400">实际比网格高 ${Math.round(hDiff)}m → 修正 <b>-${Math.abs(tDiff).toFixed(1)}°C</b></span>`
            : `<span class="text-sky-400">实际比网格低 ${Math.abs(Math.round(hDiff))}m → 修正 <b>+${Math.abs(tDiff).toFixed(1)}°C</b></span>`;

    const rowsArr = [];
    let longRangeDividerAdded = false;
    item.daily.filter(d => tripDates.includes(d.date)).forEach(d => {
        // 首个 long-range 行前插分隔标签行
        if (d.is_long_range && !longRangeDividerAdded) {
            rowsArr.push(`<tr class="long-term-divider"><td colspan="10">⚠ 8-16 天 · 长期预报 · 精度低 · 仅 Open-Meteo 长波模型</td></tr>`);
            longRangeDividerAdded = true;
        }
        const tripIdx = tripDates.indexOf(d.date);
        const tripLabel = tripIdx >= 0 ? `D${tripIdx+1}` : null;
        const isToday = d.date === (new Date().toLocaleDateString('sv-SE', {timeZone:'Asia/Shanghai'}));
        const isLongRange = !!d.is_long_range;
        const highlight = tripIdx >= 0 && !isLongRange;   // 长期段不高亮, 让灰底生效
        const b = d.badges;
        const dayLabel = tripLabel ? `<b>${tripLabel}</b>` : '';
        const todayLabel = isToday ? '<span class="text-xs text-amber-700">今天</span>' : '';
        const isSelectedDay = d.date === selectedHourlyDate;
        const dayTempDiff = (d.tmax_real - d.tmax_grid).toFixed(1);
        const dayTempDiffSign = dayTempDiff > 0 ? '+' : '';
        const glowCell = isLongRange
            ? '<span class="badge-glow low low-confidence" title="长波预报精度低, 不评分">🌅 —</span>'
            : glowBadge(d.glow);

        rowsArr.push(`
            <tr class="cursor-pointer ${highlight ? 'font-semibold' : ''} ${isSelectedDay ? 'selected-day' : ''} ${isLongRange ? 'long-term' : ''}"
                style="${highlight ? 'background:#dbeafe;' : ''}"
                onclick="selectHourlyDate('${d.date}')"
                title="${isLongRange ? '长期预报 · 精度低 · ' : ''}点击查看该日逐小时预报">
                <td><b>${d.date.slice(5)}</b><br/><span class="text-xs text-slate-500">${weekday(d.date)} ${dayLabel} ${todayLabel}</span></td>
                <td>${d.wmo_label}${d.has_thunder ? ' ⚡' : ''}</td>
                <td>
                    <b style="color:#dc2626">${d.tmax_real}°</b> / <span style="color:#0284c7">${d.tmin_real}°</span>
                    <br/><span class="text-[10px] text-slate-400">网格 ${d.tmax_grid}/${d.tmin_grid}°</span>
                    <br/><span class="text-[10px] text-amber-600">↓${dayTempDiffSign}${dayTempDiff}°</span>
                </td>
                <td>体感(WCT)<br/><b>${d.wct_night}°</b><br/><span class="text-xs text-slate-500">日 ${d.wct_day}°</span></td>
                <td>${d.precip_mm}<br/><span class="text-xs text-slate-500">${d.precip_prob}%</span></td>
                <td>${d.phase}</td>
                <td><span style="font-size:11px">${d.wind_max}<br/>阵${d.gust_max}</span></td>
                <td>${badgeMini(b.hypothermia,'失')}${badgeMini(b.thunderstorm,'雷')}${badgeMini(b.altitude,'高')}</td>
                <td>${uvBadge(d.uv)}</td>
                <td>${glowCell}</td>
            </tr>
        `);
    });
    const rows = rowsArr.join('');

    // hourly 子表 (每 3 小时一行)
    const hourlyRows = (item.hourly || [])
        .filter(h => h.time && h.time.startsWith(selectedHourlyDate))
        .filter((_, i) => i % 3 === 0)
        .map(h => {
            const cloudTxt = h.cloud != null ? `${h.cloud}%` : '—';
            const cloudLmhTxt = (h.cloud_low != null && h.cloud_mid != null && h.cloud_high != null)
                ? `<span class="text-[10px] text-slate-400">${h.cloud_low}/${h.cloud_mid}/${h.cloud_high}</span>`
                : '';
            const visTxt = h.visibility_km != null ? `${h.visibility_km}km` : '—';
            const visColor = h.visibility_km != null
                ? (h.visibility_km >= 15 ? '#16a34a' : h.visibility_km >= 8 ? '#854d0e' : '#dc2626')
                : '#475569';
            return `
            <tr>
                <td><b>${h.time.slice(11, 16)}</b></td>
                <td><b style="color:${h.t_real < 0 ? '#0284c7' : '#dc2626'}">${h.t_real}°</b><br/><span class="text-[10px] text-slate-400">网格 ${h.t_grid}°</span></td>
                <td>${h.wct}°</td>
                <td>${h.wmo_label}${h.has_thunder ? '⚡' : ''}</td>
                <td>${h.precip_mm}</td>
                <td>${h.phase}</td>
                <td><span style="font-size:11px">${h.wind}<br/>阵${h.gust}</span></td>
                <td>${h.rh}%</td>
                <td>${cloudTxt}<br/>${cloudLmhTxt}</td>
                <td><b style="color:${visColor};font-size:11px">${visTxt}</b></td>
            </tr>
        `;}).join('');

    document.getElementById('forecast-panel').innerHTML = `
        <div id="alerts-section" class="border-b"></div>
        <div class="poi-card-header">
            <div class="flex items-center justify-between">
                <div>
                    <div class="text-xs opacity-80">${TYPE_LABEL[poi.type]} · 关键节点 #${poi.id}</div>
                    <div class="text-lg font-bold">${poi.name}</div>
                </div>
                <div class="text-right text-sm">
                    <div>📍 ${poi.lat.toFixed(4)}, ${poi.lon.toFixed(4)}</div>
                    <div>⛰️ 实际 <b>${poi.ele}m</b> · 网格 ${item.h_grid ? Math.round(item.h_grid)+'m' : '?'}</div>
                    <div class="text-xs mt-1">${hDiffLabel}</div>
                </div>
            </div>
            <div class="flex gap-1 mt-2 flex-wrap">
                ${bigBadge(todayBadges.hypothermia, '失温')}
                ${bigBadge(todayBadges.thunderstorm, '雷暴')}
                ${bigBadge(todayBadges.altitude, '高反')}
            </div>
        </div>

        <details class="border-b bg-slate-50">
            <summary class="px-4 py-2 text-xs text-slate-600 cursor-pointer hover:bg-slate-100 select-none">
                ℹ️ 算法说明 (海拔修正 / 风寒WCT / UV等级 / 多源融合)
            </summary>
            <div class="px-4 py-3 text-xs text-slate-700 space-y-1.5 border-t">
                <div>📏 <b>海拔修正</b>: T_real = T_grid − Γ×(h_real−h_grid)/1000, Γ=6°C/km (湿绝热, 山区默认)</div>
                <div>❄️ <b>风寒 WCT</b>: 仅 T<10°C 且 V>4.8km/h 时启用. WCT = 13.12 + 0.62T − 11.37V^0.16 + 0.40TV^0.16</div>
                <div>☀️ <b>UV 等级</b>: 0-2 低 / 3-5 中 / 6-7 高 / 8-10 很高 / 11+ 极强</div>
                <div>🌅 <b>晚霞评分</b>: 取日落前 60min~后 30min 窗口, 按低中云(30-70%理想)+高云(<40%通透)+能见度(>15km佳)打分. ⚠️仅 Open-Meteo 参考值, 山区天气多变, 临场请复核实况</div>
                <div>🔀 <b>多源融合</b>: Open-Meteo(0.5) + 和风(0.3) + 彩云(0.2). IQR 1.5 剔除异常 + 加权平均</div>
                <div>🌨️ <b>降水相态</b>: T>3°C 雨 / T<−1°C 雪 / 中间雨夹雪 / h>3500m 且 T<0°C 雪·冰</div>
            </div>
        </details>

        <table class="forecast-table w-full">
            <thead><tr>
                <th>日期</th><th>天气</th><th>温度<br/>高/低</th><th>体感<br/>WCT夜/日</th>
                <th>降水<br/>mm/%</th><th>相态</th><th>风<br/>km/h</th><th>风险<br/>徽章</th><th>UV</th><th>🌅晚霞</th>
            </tr></thead>
            <tbody>${rows}</tbody>
        </table>

        <div class="hourly-section">
            <div class="text-xs text-slate-600 mb-2 flex items-center justify-between">
                <span>⏱️ 逐小时分层预报 · <b class="text-red-700">${selectedHourlyDate}</b> (每 3 小时一行)</span>
                <span class="text-slate-400">👆 点击上方日期行切换</span>
            </div>
            ${selDayLongRange ? '<div class="text-xs px-3 py-2 mb-2 rounded bg-red-50 border border-red-200 text-red-700">⚠ 该日处于 8-16 天长期段 · OM 长波单源 · 逐小时精度不足, 仅作参考</div>' : ''}
            <table class="forecast-table w-full">
                <thead><tr>
                    <th>时间</th><th>温度<br/>修正/网格</th><th>体感<br/>WCT</th><th>天气</th>
                    <th>降水mm</th><th>相态</th><th>风/阵风</th><th>湿度</th><th>云量%<br/>低/中/高</th><th>能见度</th>
                </tr></thead>
                <tbody>${hourlyRows || '<tr><td colspan="10" class="text-center text-slate-400 py-3">该日暂无 hourly 数据</td></tr>'}</tbody>
            </table>
        </div>

        <div id="minutely-section" class="px-4 py-3 border-t"></div>
        <div id="track-segment-section" class="px-4 py-3 border-t"></div>

        <div class="px-4 py-2 text-xs text-slate-500 border-t mt-2">
            数据源: ${item.sources.join(' + ')}${item.daily.some(d => d.is_long_range) ? ' · <span class="text-amber-700">⚠ 8-16 天段仅 Open-Meteo 长波模型, 精度低</span>' : ''} · 拉取于 ${item.fetched_at || '?'} ${item._from_cache ? '· 缓存' : '· 实时'}
        </div>
    `;
}

window.selectHourlyDate = function(date) {
    selectedHourlyDate = date;
    if (selectedPoiId && forecastMap[selectedPoiId]) {
        renderForecastPanel(forecastMap[selectedPoiId]);
    }
};

function uvBadge(uv) {
    let level, color, bg;
    if (uv < 3) { level = '低'; color = '#166534'; bg = '#dcfce7'; }
    else if (uv < 6) { level = '中'; color = '#854d0e'; bg = '#fef3c7'; }
    else if (uv < 8) { level = '高'; color = '#9a3412'; bg = '#ffedd5'; }
    else if (uv < 11) { level = '很高'; color = '#991b1b'; bg = '#fee2e2'; }
    else { level = '极强'; color = '#ffffff'; bg = '#7c2d12'; }
    return `<span class="uv-badge" style="color:${color}; background:${bg};">UV ${uv} ${level}</span>`;
}

async function loadStatus() {
    try {
        const r = await fetch('/api/status');
        const s = await r.json();
        const el = document.getElementById('last-refresh');
        const lbl = document.getElementById('refresh-label');
        if (s.in_progress) {
            el.innerHTML = '🔄 刷新中...';
            lbl.textContent = '刷新中';
        } else if (s.last_refresh) {
            el.innerHTML = `✅ 最后刷新<br/><b>${s.last_refresh.slice(11)}</b> (${s.refresh_count}次)`;
            lbl.textContent = '立即刷新';
        } else {
            el.innerHTML = '⏳ 等待首次刷新';
            lbl.textContent = '立即刷新';
        }
    } catch (e) {
        console.error('loadStatus 失败', e);
    }
}

async function manualRefresh() {
    try {
        const r = await fetch('/api/refresh');
        const d = await r.json();
        if (d.status === 'already_running') {
            alert('刷新正在进行中, 请稍候');
            return;
        }
        // 立刻更新 UI 状态
        document.getElementById('refresh-label').textContent = '刷新中';
        document.getElementById('last-refresh').innerHTML = '🔄 后台刷新中...';
        // 等 8 秒后重载 forecast
        setTimeout(async () => {
            await loadForecast();
            await loadStatus();
        }, 8000);
    } catch (e) {
        alert('刷新失败: ' + e.message);
    }
}

function bigBadge(level, label) {
    const cls = level === 'red' ? 'red' : level === 'yellow' ? 'yellow' : 'green';
    const txt = level === 'red' ? '高风险' : level === 'yellow' ? '警惕' : level === 'green' ? '注意' : '低';
    return `<span class="badge badge-${cls}"><span class="dot"></span>${label}: ${txt}</span>`;
}

function weekday(dateStr) {
    const d = new Date(dateStr + 'T00:00:00+08:00');
    return ['周日','周一','周二','周三','周四','周五','周六'][d.getDay()];
}

// ========== PDF 导出 ==========
function exportPdf() {
    if (!routeName) return;
    const url = `/api/export?route_name=${encodeURIComponent(routeName)}&date=${encodeURIComponent(departureDate)}&days=${tripDays}`;
    const a = document.createElement('a');
    a.href = url;
    a.download = `${routeName}_${departureDate}.pdf`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}

// ========== 操作指引 MD 下载 ==========
function downloadGuide() {
    const a = document.createElement('a');
    a.href = '/api/guide';
    a.download = '操作指引.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
}
