export function requestErrorMessage(error) {
  if (error instanceof TypeError && error.message === 'Failed to fetch') {
    return '控制台已停止或重启，请打开最新控制台页面';
  }
  return error instanceof Error ? error.message : '请求失败';
}
