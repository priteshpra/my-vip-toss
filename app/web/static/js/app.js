// Toss Analytics Client Helper
document.addEventListener('DOMContentLoaded', () => {
    // Console audit notice
    console.log('%c Toss Analytics & Tracking Admin Panel ', 'background: #3b82f6; color: white; font-weight: bold; padding: 4px 8px; border-radius: 4px;');
    console.log('Notice: A cricket toss is an independent fair coin flip (p=0.50). All calls are cryptographically locked pre-match.');
});

function copyToClipboard(text) {
    navigator.clipboard.writeText(text).then(() => {
        alert("Copied hash to clipboard!");
    }).catch(err => {
        console.error("Failed to copy text: ", err);
    });
}

