(function () {
  'use strict';
  var choice = document.getElementById('conversation-intention');
  if (!choice) return;
  var returning = document.getElementById('offer-returning');
  var button = document.getElementById('open-negotiation');
  var goal = document.getElementById('conversation-goal-group');
  function updateConversation() {
    document.querySelector('[data-intention-question]').hidden = !!choice.value;
    document.querySelectorAll('[data-conversation-path]').forEach(function (panel) {
      panel.hidden = panel.dataset.conversationPath !== choice.value;
    });
    goal.hidden = choice.value !== 'return' && choice.value !== 'undecided';
    var accessGuide = document.getElementById('return-access-guidance');
    if (accessGuide) accessGuide.hidden = choice.value !== 'return';
    var settlementGuide = document.getElementById('settlement-membership-guidance');
    if (settlementGuide) settlementGuide.hidden = choice.value !== 'moved' && choice.value !== 'not_returning';
    if (returning) returning.checked = choice.value === 'return';
    if (button) {
      button.disabled = !choice.value;
      document.getElementById('negotiation-guidance').textContent = !choice.value
        ? 'First select the client’s response in section 04.'
        : choice.value === 'return' ? 'Discuss repayment, then check the separate reactivation and access conditions before promising a return.'
          : 'Focus on settling the current arrears; returning is not a condition of this conversation.';
    }
    if (typeof refreshOffer === 'function') refreshOffer();
  }
  choice.addEventListener('change', updateConversation);
  if (returning) returning.addEventListener('change', function () {
    if (returning.checked) choice.value = 'return';
    else if (choice.value === 'return') choice.value = 'undecided';
    updateConversation();
  });
  if (button) button.addEventListener('click', function () {
    if (!choice.value) return;
    negotiationPreviewOpen = true;
    outcomeChanged();
    var panel = document.getElementById('offer-panel');
    panel.scrollIntoView({behavior: 'smooth', block: 'start'});
  });
  updateConversation();
}());
