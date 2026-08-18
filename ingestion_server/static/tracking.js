/*
 * ATHLEPA CRM 트래킹 스니펫.
 * 고객사 웹사이트에 이렇게 심는다:
 *   <script src=".../tracking.js" data-api-key="회사별 api_key" data-endpoint=".../track"></script>
 * 페이지가 로드되면 자동으로 page_view 이벤트를 보내고,
 * window.athlepaTrack("add_to_cart", {...}) 형태로 원하는 시점에 커스텀 이벤트도 보낼 수 있다.
 */
(function () {
  var scriptTag = document.currentScript;
  var API_KEY = scriptTag.getAttribute("data-api-key");
  var ENDPOINT = scriptTag.getAttribute("data-endpoint");

  function getOrCreateId(storageKey, prefix) {
    var id = localStorage.getItem(storageKey);
    if (!id) {
      id = prefix + "_" + Math.random().toString(36).slice(2) + Date.now().toString(36);
      localStorage.setItem(storageKey, id);
    }
    return id;
  }

  function getSessionId() {
    return getOrCreateId("athlepa_crm_session_id", "sess");
  }

  function getUserId() {
    return getOrCreateId("athlepa_crm_user_id", "anon");
  }

  window.athlepaTrack = function (eventType, props) {
    props = props || {};
    var payload = {
      api_key: API_KEY,
      event_type: eventType,
      user_id: props.user_id || getUserId(),
      session_id: getSessionId(),
      product_id: props.product_id || null,
      category: props.category || null,
      price: props.price || null,
    };
    fetch(ENDPOINT, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      keepalive: true,
    }).catch(function (err) {
      console.warn("[athlepa-crm] 이벤트 전송 실패", err);
    });
  };

  window.athlepaTrack("page_view");
})();
