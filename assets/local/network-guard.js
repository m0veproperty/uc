(() => {
  const allowed = (value) => {
    try {
      const u = new URL(String(value && value.url ? value.url : value), location.href);
      return u.origin === location.origin || u.protocol === 'data:' || u.protocol === 'blob:';
    } catch (_) { return true; }
  };
  const blocked = (kind, value) => {
    console.warn('[local-only]', kind, 'blocked external request:', value);
    return new TypeError('External network request blocked by local-only build');
  };
  if (window.fetch) {
    const original = window.fetch.bind(window);
    window.fetch = (input, init) => allowed(input) ? original(input, init) : Promise.reject(blocked('fetch', input));
  }
  if (window.XMLHttpRequest) {
    const open = XMLHttpRequest.prototype.open;
    XMLHttpRequest.prototype.open = function(method, url, ...rest) {
      if (!allowed(url)) throw blocked('xhr', url);
      return open.call(this, method, url, ...rest);
    };
  }
  if (navigator.sendBeacon) {
    const beacon = navigator.sendBeacon.bind(navigator);
    navigator.sendBeacon = (url, data) => allowed(url) ? beacon(url, data) : false;
  }
  if (window.WebSocket) {
    const NativeWebSocket = window.WebSocket;
    window.WebSocket = function(url, protocols) {
      if (!allowed(url)) throw blocked('websocket', url);
      return new NativeWebSocket(url, protocols);
    };
    window.WebSocket.prototype = NativeWebSocket.prototype;
  }
  if (window.EventSource) {
    const NativeEventSource = window.EventSource;
    window.EventSource = function(url, config) {
      if (!allowed(url)) throw blocked('eventsource', url);
      return new NativeEventSource(url, config);
    };
    window.EventSource.prototype = NativeEventSource.prototype;
  }
})();
