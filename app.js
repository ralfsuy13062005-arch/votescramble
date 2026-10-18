// Application State
const state = {
  roomId: null,
  participantId: null,
  name: null,
  isHost: false,
  hostToken: null,
  hasVoted: false,
  submittedText: '',
  roomStatus: 'submitting', // 'submitting' | 'scrambled'
  socket: null,
  myVotes: [],
  qrCodeInstance: null,
};

// Elements
const viewLobby = document.getElementById('view-lobby');
const viewRoom = document.getElementById('view-room');
const hostBanner = document.getElementById('host-banner');
const hostVoteStats = document.getElementById('host-vote-stats');
const btnScramble = document.getElementById('btn-scramble');
const navRoomBadge = document.getElementById('nav-room-badge');
const navRoomCode = document.getElementById('nav-room-code');
const displayRoomCode = document.getElementById('display-room-code');
const hostNameInput = document.getElementById('host-name-input');
const joinRoomCodeInput = document.getElementById('join-room-code-input');
const joinNameInput = document.getElementById('join-name-input');
const voteTextarea = document.getElementById('vote-textarea');
const charCounter = document.getElementById('char-counter');
const writingSpaceBox = document.getElementById('writing-space-box');
const submittedWaitingBox = document.getElementById('submitted-waiting-box');
const scrambledRevealBox = document.getElementById('scrambled-reveal-box');
const assignedVotesList = document.getElementById('assigned-votes-list');
const hostNewRoundBar = document.getElementById('host-new-round-bar');
const rosterList = document.getElementById('roster-list');
const rosterCountBadge = document.getElementById('roster-count-badge');
const qrModal = document.getElementById('qr-modal');
const qrcodeContainer = document.getElementById('qrcode-container');
const qrModalCode = document.getElementById('qr-modal-code');
const toastEl = document.getElementById('toast');
const toastMsg = document.getElementById('toast-message');

// Initialize on Load
window.addEventListener('DOMContentLoaded', () => {
  lucide.createIcons();

  // Character counter for textarea
  voteTextarea.addEventListener('input', () => {
    charCounter.textContent = `${voteTextarea.value.length} chars`;
  });

  // Check URL query parameters for ?room=ABCDE
  const urlParams = new URLSearchParams(window.location.search);
  const roomParam = urlParams.get('room');
  if (roomParam) {
    joinRoomCodeInput.value = roomParam.toUpperCase();
    joinNameInput.focus();
    
    // Check if user was already in this room
    const savedPid = localStorage.getItem(`votescramble_pid_${roomParam.toUpperCase()}`);
    const savedName = localStorage.getItem(`votescramble_name_${roomParam.toUpperCase()}`);
    const savedHostToken = localStorage.getItem(`votescramble_host_${roomParam.toUpperCase()}`);
    if (savedPid && savedName) {
      state.roomId = roomParam.toUpperCase();
      state.participantId = savedPid;
      state.name = savedName;
      state.hostToken = savedHostToken;
      state.isHost = !!savedHostToken;
      connectWebSocket();
    }
  }
});

// Navigation
function navigateHome() {
  if (state.socket) {
    state.socket.close();
  }
  window.history.pushState({}, '', '/');
  viewRoom.classList.add('hidden');
  navRoomBadge.classList.add('hidden');
  viewLobby.classList.remove('hidden');
}

// Show Toast
let toastTimeout;
function showToast(message, isError = false) {
  clearTimeout(toastTimeout);
  toastMsg.textContent = message;
  toastEl.className = `fixed bottom-6 right-6 z-50 max-w-sm px-4 py-3 rounded-xl border text-sm shadow-2xl flex items-center gap-3 transition-all duration-300 ${
    isError 
      ? 'bg-red-950/90 border-red-500/50 text-red-200' 
      : 'bg-slate-800 border-slate-700 text-white'
  }`;
  toastEl.classList.remove('translate-y-20', 'opacity-0', 'pointer-events-none');

  toastTimeout = setTimeout(() => {
    toastEl.classList.add('translate-y-20', 'opacity-0', 'pointer-events-none');
  }, 3500);
}

// Host Action: Create Room
async function handleCreateRoom() {
  const hostName = (hostNameInput.value || 'Host').trim();
  const btn = document.getElementById('btn-create-room');
  btn.disabled = true;

  try {
    const res = await fetch('/api/create-room', { method: 'POST' });
    if (!res.ok) throw new Error('Failed to create room');
    const data = await res.json();

    state.roomId = data.roomId;
    state.hostToken = data.hostToken;
    state.isHost = true;
    state.name = hostName;
    state.participantId = 'p_' + Math.random().toString(36).substring(2, 10);

    // Persist in localStorage
    localStorage.setItem(`votescramble_pid_${state.roomId}`, state.participantId);
    localStorage.setItem(`votescramble_name_${state.roomId}`, state.name);
    localStorage.setItem(`votescramble_host_${state.roomId}`, state.hostToken);

    // Update URL without page reload
    window.history.pushState({}, '', `/?room=${state.roomId}`);

    connectWebSocket();
  } catch (err) {
    showToast(err.message, true);
  } finally {
    btn.disabled = false;
  }
}

// Participant Action: Join Room
function handleJoinRoom() {
  const code = joinRoomCodeInput.value.trim().toUpperCase();
  const name = (joinNameInput.value || 'Anonymous').trim();

  if (!code) {
    showToast('Please enter a room code', true);
    joinRoomCodeInput.focus();
    return;
  }

  state.roomId = code;
  state.name = name;
  state.hostToken = localStorage.getItem(`votescramble_host_${code}`) || null;
  state.isHost = !!state.hostToken;
  state.participantId = localStorage.getItem(`votescramble_pid_${code}`) || ('p_' + Math.random().toString(36).substring(2, 10));

  localStorage.setItem(`votescramble_pid_${code}`, state.participantId);
  localStorage.setItem(`votescramble_name_${code}`, state.name);

  window.history.pushState({}, '', `/?room=${state.roomId}`);
  connectWebSocket();
}

// WebSocket Management
function connectWebSocket() {
  if (state.socket) {
    state.socket.close();
  }

  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const wsUrl = `${protocol}//${window.location.host}/ws/${state.roomId}`;
  const socket = new WebSocket(wsUrl);
  state.socket = socket;

  socket.onopen = () => {
    socket.send(JSON.stringify({
      type: 'join',
      participantId: state.participantId,
      name: state.name,
      hostToken: state.hostToken
    }));
  };

  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      handleSocketMessage(data);
    } catch (e) {
      console.error('Invalid message from server:', e);
    }
  };

  socket.onclose = () => {
    // Attempt reconnection after 2 seconds if still in room
    if (state.roomId && !viewRoom.classList.contains('hidden')) {
      setTimeout(() => {
        if (state.socket === socket) connectWebSocket();
      }, 2500);
    }
  };

  socket.onerror = (err) => {
    console.error('WebSocket error:', err);
  };
}

// Handle Server Messages
function handleSocketMessage(msg) {
  if (msg.type === 'error') {
    showToast(msg.message, true);
    return;
  }

  if (msg.type === 'joined') {
    state.isHost = msg.isHost;
    switchToRoomView();
    return;
  }

  if (msg.type === 'room_state') {
    updateRoomState(msg);
  }
}

// Switch UI from Lobby to Room
function switchToRoomView() {
  viewLobby.classList.add('hidden');
  viewRoom.classList.remove('hidden');

  navRoomCode.textContent = state.roomId;
  displayRoomCode.textContent = state.roomId;
  qrModalCode.textContent = state.roomId;
  navRoomBadge.classList.remove('hidden');

  if (state.isHost) {
    hostBanner.classList.remove('hidden');
    hostNewRoundBar.classList.remove('hidden');
  } else {
    hostBanner.classList.add('hidden');
    hostNewRoundBar.classList.add('hidden');
  }

  lucide.createIcons();
}

// Update Room View on State Change
function updateRoomState(data) {
  state.roomStatus = data.status;
  state.hasVoted = data.hasVoted;
  state.myVotes = data.myVotes || [];

  // Update Host Banner Stats
  hostVoteStats.textContent = `${data.votesCount} of ${data.participants.length} submitted`;
  btnScramble.disabled = (data.votesCount === 0);

  // Update Participants Roster
  renderRoster(data.participants);

  // Manage UI view states:
  if (state.roomStatus === 'scrambled') {
    // PHASE 2: REVEAL
    writingSpaceBox.classList.add('hidden');
    submittedWaitingBox.classList.add('hidden');
    scrambledRevealBox.classList.remove('hidden');
    renderAssignedVotes();
  } else {
    // PHASE 1: WRITING / SUBMITTING
    scrambledRevealBox.classList.add('hidden');
    if (state.hasVoted) {
      writingSpaceBox.classList.add('hidden');
      submittedWaitingBox.classList.remove('hidden');
    } else {
      submittedWaitingBox.classList.add('hidden');
      writingSpaceBox.classList.remove('hidden');
    }
  }

  lucide.createIcons();
}

// Render Participants Roster
function renderRoster(participants) {
  rosterCountBadge.textContent = participants.length;
  rosterList.innerHTML = '';

  participants.forEach(p => {
    const isMe = p.id === state.participantId;
    const li = document.createElement('li');
    li.className = `p-2.5 rounded-xl border flex items-center justify-between text-xs sm:text-sm transition ${
      isMe 
        ? 'bg-slate-700/60 border-indigo-500/40 text-white' 
        : 'bg-slate-900/50 border-slate-800 text-slate-300'
    }`;

    // Name & Badges
    const nameCol = document.createElement('div');
    nameCol.className = 'flex items-center gap-2 truncate';

    const avatar = document.createElement('div');
    avatar.className = `w-7 h-7 rounded-lg flex items-center justify-center text-xs font-bold shrink-0 ${
      p.isHost ? 'bg-indigo-500/20 text-indigo-300' : 'bg-slate-800 text-slate-400'
    }`;
    avatar.textContent = (p.name || '?')[0].toUpperCase();
    nameCol.appendChild(avatar);

    const nameSpan = document.createElement('span');
    nameSpan.className = 'truncate font-medium';
    nameSpan.textContent = p.name + (isMe ? ' (You)' : '');
    nameCol.appendChild(nameSpan);

    if (p.isHost) {
      const hostBadge = document.createElement('span');
      hostBadge.className = 'text-[10px] font-semibold px-1.5 py-0.5 rounded bg-indigo-500/20 text-indigo-300';
      hostBadge.textContent = 'HOST';
      nameCol.appendChild(hostBadge);
    }

    // Status Badge
    const statusCol = document.createElement('div');
    statusCol.className = 'shrink-0 flex items-center gap-1.5';

    if (!p.connected) {
      statusCol.innerHTML = `<span class="text-[11px] text-slate-500">Offline</span>`;
    } else if (p.voted) {
      statusCol.innerHTML = `
        <span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium">
          <i data-lucide="check" class="w-3 h-3"></i> Ready
        </span>`;
    } else {
      statusCol.innerHTML = `
        <span class="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-amber-500/10 text-amber-400 border border-amber-500/20 font-medium">
          <i data-lucide="pen-tool" class="w-3 h-3 animate-pulse"></i> Writing
        </span>`;
    }

    li.appendChild(nameCol);
    li.appendChild(statusCol);
    rosterList.appendChild(li);
  });

  lucide.createIcons();
}

// Submit Vote
function handleSubmitVote() {
  const text = voteTextarea.value.trim();
  if (!text) {
    showToast('Please type your vote or thought first', true);
    voteTextarea.focus();
    return;
  }

  state.submittedText = text;
  state.socket.send(JSON.stringify({
    type: 'submit_vote',
    text: text
  }));

  showToast('Vote submitted successfully!');
}

// Edit Vote (before scramble)
function handleEditVote() {
  voteTextarea.value = state.submittedText;
  charCounter.textContent = `${voteTextarea.value.length} chars`;
  submittedWaitingBox.classList.add('hidden');
  writingSpaceBox.classList.remove('hidden');
  voteTextarea.focus();
}

// Scramble Trigger (Host Only)
function handleScramble() {
  if (!state.isHost) return;
  state.socket.send(JSON.stringify({
    type: 'scramble',
    hostToken: state.hostToken
  }));
}

// Render Assigned Scrambled Votes
let hasCelebrated = false;
function renderAssignedVotes() {
  assignedVotesList.innerHTML = '';

  if (!state.myVotes || state.myVotes.length === 0) {
    assignedVotesList.innerHTML = `
      <div class="text-center py-8 text-slate-400 text-sm">
        No vote assigned to you. (You may have joined after the scramble).
      </div>
    `;
    return;
  }

  // Trigger Confetti Celebration once upon reveal
  if (!hasCelebrated && typeof confetti === 'function') {
    hasCelebrated = true;
    confetti({
      particleCount: 80,
      spread: 70,
      origin: { y: 0.6 }
    });
  }

  state.myVotes.forEach((vote, idx) => {
    const isBonus = idx > 0;
    const card = document.createElement('div');
    card.className = `p-6 rounded-2xl border transition-all ${
      isBonus 
        ? 'bg-slate-900/90 border-amber-500/40 shadow-lg' 
        : 'bg-slate-900 border-indigo-500/40 shadow-xl'
    }`;

    card.innerHTML = `
      <div class="flex items-center justify-between mb-3">
        <span class="text-xs uppercase font-mono tracking-wider font-semibold ${
          isBonus ? 'text-amber-400' : 'text-indigo-400'
        }">
          ${isBonus ? '★ Extra Assigned Vote (from disconnected player)' : 'Assigned Vote'}
        </span>
        <button onclick="copyVoteText(this)" data-text="${encodeURIComponent(vote.text)}" class="p-1.5 hover:bg-slate-800 text-slate-400 hover:text-white rounded-lg transition" title="Copy text">
          <i data-lucide="copy" class="w-4 h-4"></i>
        </button>
      </div>
      <div class="text-slate-100 text-base sm:text-lg leading-relaxed whitespace-pre-wrap font-medium">
        ${escapeHtml(vote.text)}
      </div>
    `;

    assignedVotesList.appendChild(card);
  });

  lucide.createIcons();
}

// Start New Round (Host Only)
function handleNewRound() {
  if (!state.isHost) return;
  if (!confirm('Start a new round? This will clear current votes and give everyone a fresh writing space.')) return;

  hasCelebrated = false;
  voteTextarea.value = '';
  charCounter.textContent = '0 chars';
  state.socket.send(JSON.stringify({
    type: 'new_round',
    hostToken: state.hostToken
  }));
}

// QR Code Modal Toggle
function toggleQRModal(show) {
  if (show) {
    qrModal.classList.remove('hidden');
    generateQRCode();
  } else {
    qrModal.classList.add('hidden');
  }
}

// Generate QR Code
function generateQRCode() {
  qrcodeContainer.innerHTML = '';
  const inviteUrl = `${window.location.origin}/?room=${state.roomId}`;
  
  if (typeof QRCode !== 'undefined') {
    new QRCode(qrcodeContainer, {
      text: inviteUrl,
      width: 180,
      height: 180,
      colorDark: '#0f172a',
      colorLight: '#ffffff',
      correctLevel: QRCode.CorrectLevel.M
    });
  }
}

// Copy Invite Link
function copyInviteLink() {
  const inviteUrl = `${window.location.origin}/?room=${state.roomId}`;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(inviteUrl).then(() => {
      showToast('Invite link copied to clipboard!');
    }).catch(() => {
      prompt('Copy this link:', inviteUrl);
    });
  } else {
    prompt('Copy this link:', inviteUrl);
  }
}

// Copy Vote Text
function copyVoteText(btn) {
  const text = decodeURIComponent(btn.getAttribute('data-text'));
  navigator.clipboard.writeText(text).then(() => {
    showToast('Vote text copied!');
  });
}

// Utility: Escape HTML
function escapeHtml(text) {
  const map = {
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#039;'
  };
  return text.replace(/[&<>"']/g, m => map[m]);
}
