// Transport-free state shared by the workbench and its regression tests.
export class PreviewSelection {
  select(session, resource, name) {
    if (this.session !== session || this.resource !== resource) {
      this.session = session;
      this.resource = resource;
      this.token = {};
      this.url = null;
      this.loading = false;
      this.error = '';
    }
    this.name = name;
    return this.token;
  }

  accept(token, url) {
    if (token !== this.token) return false;
    this.url = url;
    return true;
  }
}

export function percentages(rows) {
  if (rows.some(row => row.group)) {
    const output = rows.map(() => 0);
    for (const group of ['standby', 'feature']) {
      const indices = rows.map((row, i) => row.group === group ? i : -1).filter(i => i >= 0);
      const shares = percentages(indices.map(i => ({weight: rows[i].weight})));
      indices.forEach((i, j) => { output[i] = shares[j]; });
    }
    return output;
  }
  const total = rows.reduce((sum, row) => sum + row.weight, 0);
  if (!total) return rows.map(() => 0);
  const units = rows.map(row => Math.floor(row.weight * 10000 / total));
  const order = rows.map((row, index) => ({index, remainder: row.weight * 10000 % total}));
  order.sort((a, b) => b.remainder - a.remainder || a.index - b.index);
  const missing = 10000 - units.reduce((sum, value) => sum + value, 0);
  for (let i = 0; i < missing; i++) units[order[i].index]++;
  return units.map(value => value / 100);
}

export function resourceOptions(catalog, track) {
  const asset = {expression: 'animation', motion: 'action', sound: 'sound'}[track];
  return (catalog?.expressions || [])
    .filter(item => item.assets?.[asset])
    .map(item => ({id: item.id, name: item.display_name || item.id}));
}

export function readSession(data) {
  // A missing profile is an empty device, never permission to seed demo rows.
  const session = {
    port: data.port, device_id: data.device_id, revision: data.revision,
    editable: data.editable === true, catalog: data.catalog,
    supportsPools: data.device?.capabilities?.includes('joy_idle_pools_v1') === true,
    schema: data.profile?.schema || 1,
    pool_percentages: structuredClone(data.profile?.pool_percentages || null),
    rows: structuredClone(data.profile?.behaviors || []),
  };
  // Upgrade the editing draft only; USB writes still require Save.
  if (session.editable && session.supportsPools && session.schema === 2 &&
      session.rows.some(row => row.weight > 0 && ['standby', 'feature'].includes(row.group))) {
    return {...enablePoolSelection(session), pendingPoolMigration: true};
  }
  return session;
}

export function enablePoolSelection(session) {
  if (!session.supportsPools || session.schema !== 2) throw new Error('当前固件不支持升级分组概率');
  const standby = session.rows.some(row => row.group === 'standby' && row.weight > 0);
  const feature = session.rows.some(row => row.group === 'feature' && row.weight > 0);
  if (!standby && !feature) throw new Error('请先启用至少一个行为');
  return {...session, schema: 3, pool_percentages: {
    standby: standby ? (feature ? 50 : 100) : 0,
    feature: feature ? (standby ? 50 : 100) : 0,
  }};
}

export function saveRequest(session, rows) {
  if (!session?.editable || !session.device_id || !Number.isInteger(session.revision)) {
    throw new Error('请先读取支持编辑的设备');
  }
  return {port: session.port, device_id: session.device_id, revision: session.revision,
    profile: {schema: session.schema || 1, behaviors: structuredClone(rows),
      ...(session.schema===3?{pool_percentages:structuredClone(session.pool_percentages)}:{})}};
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') {
    return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  }
  return value;
}

export function confirmSave(request, result) {
  if (result?.device_id !== request.device_id || result.revision !== request.revision + 1 ||
      JSON.stringify(canonical(result.profile)) !== JSON.stringify(canonical(request.profile))) {
    throw new Error('回读校验不一致，请重新读取设备');
  }
}
