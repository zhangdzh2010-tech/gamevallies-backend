// ===================== Init =====================
function init() {
  applyRangePreset('dashboard', '30d', false);
  applyRangePreset('subscriptions', '30d', false);
  loadStats();
}

// ESC key closes play/preview overlays
document.addEventListener('keydown', e => {
  if (e.key === 'Escape') {
    if (document.getElementById('playOverlay').style.display !== 'none') closePlay();
    else if (document.getElementById('coverPreviewOverlay').style.display !== 'none') closeCoverPreview();
    else if (document.getElementById('previewOverlay').style.display !== 'none') closePreview();
  }
});

checkAuth();
