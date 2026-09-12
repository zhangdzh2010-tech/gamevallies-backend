// ===================== Init =====================
function init() {
  applyRangePreset('dashboard', '30d', false);
  applyRangePreset('subscriptions', '30d', false);
  loadStats();
}

// ESC closes sheets / play / preview. Detail modals register their own listener.
document.addEventListener('keydown', e => {
  if (e.key !== 'Escape') return;
  if (document.querySelector('.confirm-overlay')) return;
  if (document.querySelector('.modal-overlay')) return;
  const promptModal = document.getElementById('promptModal');
  if (promptModal && promptModal.style.display === 'flex') {
    closePromptModal();
    return;
  }
  const activeSheet = document.querySelector('.sheet-overlay.active');
  if (activeSheet) {
    if (activeSheet.id === 'llm3Sheet') closeLlmConfigSheet();
    else if (activeSheet.id === 'subscriptionPlanSheet') closeSubscriptionPlanSheet();
    else activeSheet.classList.remove('active');
    return;
  }
  if (document.getElementById('playOverlay').style.display !== 'none') closePlay();
  else if (document.getElementById('coverPreviewOverlay').style.display !== 'none') closeCoverPreview();
  else if (document.getElementById('previewOverlay').style.display !== 'none') closePreview();
});

checkAuth();
