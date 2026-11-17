import {percentages} from './state.mjs';

export function tableRows(rows, baseline, group = 'all', query = '', catalog = null) {
 const shares = percentages(rows);
 const needle = query.trim().toLowerCase();
 return rows.map((row, index) => ({row, index, share: shares[index],
  changed: JSON.stringify(row) !== JSON.stringify(baseline.find(item => item.id === row.id)),
 })).filter(({row}) => (group === 'all' || row.group === group) &&
  [row.name, row.expression, row.motion, row.sound, ...(catalog?.expressions || []).filter(item => [row.expression,row.motion,row.sound].includes(item.id)).map(item => item.display_name)].some(value => String(value || '').toLowerCase().includes(needle)));
}

export function normalizeGroups(rows, groups) {
 for (const group of new Set(groups)) {
  const pool=rows.filter(row=>row.group===group);
  const total=pool.reduce((sum,row)=>sum+row.weight,0);
  if(!total)continue;
  const allocation=pool.map((row,index)=>({row,index,units:Math.floor(row.weight*100/total),remainder:row.weight*100%total}));
  let remaining=100-allocation.reduce((sum,item)=>sum+item.units,0);
  allocation.sort((a,b)=>b.remainder-a.remainder||a.index-b.index);
  for(let i=0;i<remaining;i++)allocation[i].units++;
  allocation.forEach(item=>{item.row.weight=item.units;});
 }
}

export function updateCell(row, key, value) {
 if (key === 'weight') {
  const number = Number(value);
  if (!String(value).trim() || !Number.isInteger(number) || number < 0 || number > 1000000) return false;
  row.weight = number;
 } else if (key === 'name') {
  if (!value.trim() || value.trim().length > 32) return false;
  row.name = value.trim();
 } else if (['expression', 'motion', 'sound', 'group'].includes(key)) {
  row[key] = value || null;
 } else return false;
 return true;
}

// Use integer percentages on the wire; largest remainders keep the pool at 100.
export function setProbability(rows, id, value) {
 const target=rows.find(row=>row.id===id), number=Number(value);
 if(!target || !String(value).trim() || !Number.isInteger(number) || number<1 || number>100)return false;
 const others=rows.filter(row=>row!==target && row.group===target.group);
 if(!others.length && number!==100)return false;
 let candidates=others.filter(row=>row.weight>0);
 if(!candidates.length)candidates=others;
 const total=candidates.reduce((sum,row)=>sum+row.weight,0);
 const allocations=candidates.map((row,index)=>{const exact=(100-number)*(total?row.weight/total:1/candidates.length);return {row,index,units:Math.floor(exact),fraction:exact-Math.floor(exact)};});
 const remaining=100-number-allocations.reduce((sum,item)=>sum+item.units,0);
 allocations.sort((a,b)=>b.fraction-a.fraction||a.index-b.index);
 for(let i=0;i<remaining;i++)allocations[i].units++;
 others.forEach(row=>{row.weight=0;});
 allocations.forEach(item=>{item.row.weight=item.units;});target.weight=number;
 return true;
}
