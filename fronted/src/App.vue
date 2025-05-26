<template>
  <div id="app">
    <h1>协同白板 </h1>

    <div class="control-area">
      <label for="room-input">房间号：</label>
      <input type="text" id="room-input" v-model="room" placeholder="请输入房间号">

      <button @click="joinRoom" :disabled="isJoinButtonDisabled">加入房间</button>
      <button @click="exitRoom" :disabled="!isConnected || !currentRoom">退出房间</button>

      <span v-if="isConnected">已连接，房间: {{ currentRoom }} <span v-if="myUserId !== null">(您的ID: {{ myUserId }})</span> <span v-if="isHost">(您是主机)</span></span>
       <span v-else style="color:gray;">未连接</span>
    </div>

    <div class="control-area">
      <label for="color-picker">颜色：</label>
      <input type="color" id="color-picker" v-model="color">

      <label for="linewidth-slider" v-if="currentTool === 'pen' || currentTool === 'eraser'">画笔粗细：</label>
       <input type="range" id="linewidth-slider" min="1" max="10" v-model="lineWidth"
               v-if="currentTool === 'pen' || currentTool === 'eraser'">
       <span v-if="currentTool === 'pen' || currentTool === 'eraser'">{{ lineWidth }}</span>

       <span v-if="currentTool === 'text'">
           <label for="font-size-select">字号:</label>
           <select id="font-size-select" v-model="fontSize" @change="updateFont">
               <option v-for="size in availableFontSizes" :key="size" :value="size">{{ size }}px</option>
           </select>

           <label for="font-family-select">字体:</label>
           <select id="font-family-select" v-model="fontFamily" @change="updateFont">
               <option v-for="font in availableFonts" :key="font" :value="font">{{ font }}</option>
           </select>
       </span>


      <button @click="setTool('pen')" :class="{ active: currentTool === 'pen' }">画笔</button>
      <button @click="setTool('eraser')" :class="{ active: currentTool === 'eraser' }">橡皮擦</button>
      <button @click="setTool('text')" :class="{ active: currentTool === 'text' }">文本</button>
      <button @click="clearCanvas" :disabled="isClearDisabled">清空</button>
    </div>

    <div class="control-area user-management" v-if="isConnected && currentRoom">
         <h3>在线用户 ({{ users.length }})</h3>
         <ul>
             <li v-for="user in users" :key="user.id" :class="{ 'is-host': user.isHost, 'is-me': user.id === myUserId }">
                 ID: {{ user.id }} <span v-if="user.isHost">(主机)</span> <span v-if="user.id === myUserId">(我)</span> <span v-if="getUserConnectionStatus(user.id)">[已连接DC]</span>
             </li>
         </ul>

         <div class="host-transfer" v-if="isHost && otherUsers.length > 0">
             <h4>移交主机权限</h4>
             <select v-model="selectedTransferUserId">
                 <option disabled value="">请选择用户</option>
                 <option v-for="user in otherUsers" :key="user.id" :value="user.id">
                     ID: {{ user.id }}
                 </option>
             </select>
             <button @click="transferHost" :disabled="selectedTransferUserId === null">移交</button>
         </div>
          <div class="host-transfer" v-else-if="isHost && otherUsers.length === 0">
              <p>当前房间暂无其他用户，无法移交主机。</p>
          </div>
     </div>


    <canvas id="whiteboard" width="800" height="600"
            @mousedown="startDrawing"
            @mouseup="stopDrawing"
            @mousemove="drawing"
            @mouseleave="stopDrawing"></canvas>
  </div>
</template>

<script>
// WebRTC 配置
const rtcConfig = {
  iceServers: [
    { urls: 'stun:stun.l.google.com:19302' } // 使用 Google 的公共 STUN 服务器
    // 可以在这里添加 TURN 服务器
  ]
};

export default {
  name: 'App',
  data() {
    return {
      isDrawing: false,
      color: '#000000',
      lineWidth: 2,
      lastX: 0,
      lastY: 0,
      ctx: null,
      room: '', // 用户输入的房间号
      currentRoom: '', // 成功加入的房间号
      websocket: null, // WebSocket 连接 (用于信令)
      currentTool: 'pen', // 当前工具：'pen', 'eraser', or 'text'
      isConnected: false, // 标记 WebSocket 是否已连接成功
      isHost: false, // 标记当前客户端是否是房间的主机
      myUserId: null, // 新增: 当前客户端在房间中的唯一ID
      users: [], // 新增: 房间内的用户列表 { id: number, isHost: boolean }
      selectedTransferUserId: null, // 新增: 主机移交时选中的目标用户ID

       // --- 文本工具相关状态 (简化方案先硬编码，后续可添加UI) ---
       fontSize: 16, // 默认字号
       fontFamily: 'Arial', // 默认字体
       // 可选的字体和字号列表
       availableFonts: ['Arial', 'Verdana', 'Times New Roman', 'Courier New', 'Georgia', '宋体', '黑体', '楷体', '等线'],
       availableFontSizes: [12, 14, 16, 18, 20, 24, 28, 32, 36, 40],


      // --- WebRTC 相关状态 ---
      peerConnections: {}, // { targetUserId: RTCPeerConnection }
      dataChannels: {}, // { targetUserId: RTCDataChannel }
      // dataChannelOpenStatus: {} // Optional: track DC readyState more explicitly { targetUserId: boolean }
    }
  },
  computed: {
      isJoinButtonDisabled() {
          if (!this.room) {
              return true;
          }
          if (this.websocket) {
              // CONNECTING (0), OPEN (1)
              if (this.websocket.readyState === WebSocket.CONNECTING || this.websocket.readyState === WebSocket.OPEN) {
                  return true;
              }
          }
          return false;
      },
      isClearDisabled() {
          // 只有当不是主机，并且已经连接且加入房间后才禁用
          return !this.isHost && this.isConnected && this.currentRoom;
      },
      // 计算属性，获取除当前用户外，房间内的其他用户列表，用于主机移交
      otherUsers() {
          if (!this.users || this.myUserId === null) return [];
          // 过滤掉当前用户自己
          return this.users.filter(user => user.id !== this.myUserId);
      }
  },
  mounted() {
    const canvas = document.getElementById('whiteboard');
    this.ctx = canvas.getContext('2d');
    this.ctx.lineCap = 'round';
    this.setTool(this.currentTool);

     // 页面加载时，如果URL中有room参数，自动填充房间号输入框
     const urlParams = new URLSearchParams(window.location.search);
     const roomFromUrl = urlParams.get('room');
     if (roomFromUrl) {
         this.room = roomFromUrl;
     }
  },
  watch: {
    // 只在画笔模式下监听颜色和线宽，文本模式下颜色另设
    color(newColor) {
      if (this.currentTool === 'pen') {
        this.ctx.strokeStyle = newColor;
      }
    },
    lineWidth(newLineWidth) {
       const parsedLineWidth = parseInt(newLineWidth);
       if (this.currentTool === 'pen') {
           this.ctx.lineWidth = parsedLineWidth;
       } else if (this.currentTool === 'eraser') {
           // 橡皮擦可以比画笔粗一些
           this.ctx.lineWidth = parsedLineWidth + 5;
       }
    },
    // 当主机状态变化时，如果从主机变为非主机，重置移交选择
    isHost(newVal) {
        if (!newVal) {
            this.selectedTransferUserId = null;
        }
    },
      // 当用户列表变化时，如果当前选中的移交对象不再列表中，重置选择
      users() {
          if (this.selectedTransferUserId !== null) {
              const targetExists = this.users.some(user => user.id === this.selectedTransferUserId);
              if (!targetExists) {
                  this.selectedTransferUserId = null;
              }
          }
          // --- WebRTC 用户列表变化处理 ---
          // 根据最新的用户列表，建立/关闭与其他用户的 WebRTC 连接
          // Note: users watcher is triggered by the server sending new user list
          // which happens after join, leave, or host transfer.
          this.updatePeerConnections(this.users.map(u => u.id));
      }
  },
  methods: {
    // --- WebRTC Helper: 获取连接状态 ---
    getUserConnectionStatus(userId) {
        // Check if a Data Channel exists and is open for this user
        const dc = this.dataChannels[userId];
        return dc && dc.readyState === 'open';
    },

    setTool(tool) {
        this.currentTool = tool;
         // Reset some drawing state when switching tools
         this.isDrawing = false; // Stop any ongoing drawing if switching

         // Update canvas context style based on selected tool
         if (tool === 'pen') {
             // Local drawing uses current color/width
             this.ctx.strokeStyle = this.color;
             this.ctx.lineWidth = parseInt(this.lineWidth);
             this.ctx.globalCompositeOperation = 'source-over'; // 正常模式
         } else if (tool === 'eraser') {
            // Local drawing uses white
            this.ctx.strokeStyle = '#FFFFFF'; // 本地橡皮擦用白色画
            this.ctx.lineWidth = parseInt(this.lineWidth) + 5; // 橡皮擦线宽
             this.ctx.globalCompositeOperation = 'destination-out'; // destination-out 实现擦除效果 (本地)
         } else if (tool === 'text') {
            // Text tool doesn't draw lines, just prepare for text input
             this.ctx.globalCompositeOperation = 'source-over'; // Ensure normal mode for text
            //  this.ctx.fillStyle = this.color; // Text color will be handled when drawing
            //  this.ctx.font = `${this.fontSize}px ${this.fontFamily}`; // Font style will be set when drawing
         }
         this.ctx.lineCap = 'round'; // Ensure round caps for drawing tools

         console.log("Switched tool to:", this.currentTool);
    },
    // 新增方法：更新字体和字号
    updateFont() {
      // 当字体或字号选择框变化时，这个方法会被调用
      // 这里的逻辑主要是确保 `fontSize` 和 `fontFamily` 状态已经更新
      // 文本工具在 `addTextToCanvas` 中会使用这些更新后的值
      console.log(`字体更新为: ${this.fontFamily}, 字号更新为: ${this.fontSize}px`);
    },
    connectWebSocket() {
      if (this.websocket && (this.websocket.readyState === WebSocket.OPEN || this.websocket.readyState === WebSocket.CONNECTING)) {
        console.log('WebSocket 正在连接或已连接，无需重复操作');
        return;
      }

      // 使用当前页面的 hostname 连接 WebSocket
      this.websocket = new WebSocket(`ws://${window.location.hostname}:8765`);

      this.websocket.onopen = () => {
        console.log('WebSocket 连接已建立');
        this.isConnected = true;
        this.isHost = false; // 连接建立时，默认不是主机
        this.myUserId = null; // 重置用户ID
        this.users = []; // 清空用户列表
        this.selectedTransferUserId = null; // 重置移交选择

        // Reset WebRTC state
        this.closeAllPeerConnections(); // Ensure previous connections are closed
        this.peerConnections = {};
        this.dataChannels = {};
        // this.dataChannelOpenStatus = {};

        if (this.room) {
           this.sendJoinMessage(this.room);
        }
      };

      this.websocket.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          // console.log('收到消息:', data);
          this.handleServerMessage(data);

        } catch (error) {
          console.error('解析消息失败:', error, event.data);
        }
      };

      this.websocket.onclose = (event) => {
        console.log('WebSocket 连接已关闭:', event.code, event.reason);
        this.cleanupConnection(); // 调用统一的清理方法
         // 可以在这里根据 event.code 判断是否需要尝试重连
      };

      this.websocket.onerror = (error) => {
        console.error('WebSocket 发生错误:', error);
         // 错误通常伴随连接关闭，onclose 会被触发
      };
    },
    // 统一处理服务器消息 (主要用于信令和其他控制消息)
    handleServerMessage(data) {
         switch (data.type) {
             case 'joined':
                 // 成功加入房间的确认消息
                 this.currentRoom = data.room;
                 this.myUserId = data.userId; // 设置当前用户的ID
                 this.isHost = data.isHost || false; // 从服务器获取是否是主机的信息
                 console.log(`成功加入房间: ${this.currentRoom}, 您的ID: ${this.myUserId}, 您是主机: ${this.isHost}`);
                 // 清空画布，准备接收历史数据或从空白开始
                 // 在当前实现下，新用户默认看到空白画布
                 if (this.ctx) {
                   this.ctx.clearRect(0, 0, this.ctx.canvas.width, this.ctx.canvas.height);
                   console.log("新加入房间，清空本地画布。");
                 }
                 // 可以选择更新URL中的房间号
                 this.updateUrlRoom(this.currentRoom);
                 break;

             case 'user-list': { // 使用块级作用域
                 console.log(`收到用户列表 (${data.users.length}人):`, data.users);
                 // Update this client's user list
                 const newUserIds = new Set(data.users.map(u => u.id));

                 this.users = data.users; // Vue 3 watches array changes directly

                 // Based on user list changes, initiate or close WebRTC connections
                 // This is triggered by join, leave, host transfer, etc.
                 this.updatePeerConnections(newUserIds);


                 // 根据用户列表更新自己的主机状态 (这是最权威的状态来源)
                 const me = this.users.find(user => user.id === this.myUserId);
                 if (me) {
                     // Note: isHost state was potentially set immediately on client-side in transferHost
                     // This server-side update will confirm the state.
                     // If we were transferred host *to* us, this updates isHost to true.
                     // If we transferred host *away* from us, this confirms isHost is false.
                     this.isHost = me.isHost;
                 } else {
                     // 如果自己在用户列表里找不到了，可能是服务器出错了或者自己即将断开
                     console.warn("自己的用户ID未在服务器用户列表中找到!");
                     this.isHost = false; // Assume not host if not in list
                     // This scenario might indicate a pending disconnect/cleanup.
                 }
                 break;
             }

             case 'clear':
               // 接收到清空白板指令（通过 WS 转发）
               console.log(`收到清空白板指令 (由用户 ${data.userId} 发送)`);
               if (this.ctx) {
                 this.ctx.clearRect(0, 0, this.ctx.canvas.width, this.ctx.canvas.height);
               }
               break;

            // --- WebRTC Signaling Messages ---
            case 'offer':
            case 'answer':
            case 'candidate':
                 console.log(`收到 WebRTC 信令 (${data.type}) 从用户 ID: ${data.senderUserId}`);
                 if (data.senderUserId === this.myUserId) {
                     console.warn("收到自己发送的信令消息，忽略。");
                     return;
                 }
                 // Handle the received signaling message
                 this.handleSignalingMessage(data.senderUserId, data.type, data.data);
                 break;

            case 'drawing':
              // !!! IMPORTANT: Drawing data should now come via Data Channel, not WebSocket.
              // If you see this message, it means the old WebSocket drawing logic is still active somewhere,
              // or the server is still broadcasting drawing messages via WS.
              console.warn("收到通过 WebSocket 发送的 'drawing' 消息，这不应该是 WebRTC Data Channel 的行为。");
              // Optionally process it for backward compatibility during transition, but ideally, this case should be removed later.
              // this.drawOnCanvas(data.data);
              break;

            case 'info':
                 console.log("服务器信息:", data.message);
                 // Display info message to user (e.g., using a notification system)
                 // alert(data.message);
                 break;
            case 'error':
                console.error("服务器返回错误:", data.message);
                alert(`服务器错误: ${data.message}`);
                // Depending on the error, you might want to force disconnect/reconnect
                break;
            default:
              console.warn('收到未知消息类型:', data.type, data);
          }
    },
    sendJoinMessage(room) {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
             const joinMessage = JSON.stringify({
                type: 'join',
                room: room
             });
             this.websocket.send(joinMessage);
             console.log('发送加入房间消息:', joinMessage);
        } else {
            console.warn('WebSocket 未连接或未打开，无法发送加入消息');
        }
    },
    joinRoom() {
      if (this.isJoinButtonDisabled) {
          console.log("加入按钮被禁用，不执行加入逻辑");
          return;
      }

      // 确保房间号不为空
      if (!this.room) {
          alert("请输入房间号");
          return;
      }

      // 如果 WebSocket 未连接或已关闭，先连接
      if (!this.websocket || this.websocket.readyState === WebSocket.CLOSED) {
          console.log('WebSocket 未连接或已关闭，尝试连接...');
          this.connectWebSocket();
      } else if (this.websocket.readyState === WebSocket.CONNECTING) {
          console.log('WebSocket 正在连接中，请稍候...');
      } else if (this.websocket.readyState === WebSocket.OPEN) {
          // WebSocket 已连接，直接发送加入消息
          this.sendJoinMessage(this.room);
      }
    },
    // 新增：退出房间方法
    exitRoom() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN && this.currentRoom) {
             const leaveMessage = JSON.stringify({
                 type: 'leave',
                 room: this.currentRoom,
                 userId: this.myUserId // 带上用户ID
             });
             this.websocket.send(leaveMessage);
             console.log('发送退出房间消息');

             // 关闭 WebSocket 连接，这将触发 onclose 和 cleanupConnection
             this.websocket.close();

        } else {
            console.warn('WebSocket 未连接或未加入房间，无法退出');
            // 如果连接已关闭但状态显示已连接，强制清理本地状态
            if (!this.websocket || this.websocket.readyState === WebSocket.CLOSED) {
                 this.cleanupConnection();
            }
        }
    },
    // 新增：清理连接相关状态的方法
    cleanupConnection() {
        this.websocket = null;
        this.currentRoom = '';
        this.isConnected = false;
        this.isHost = false;
        this.myUserId = null;
        this.users = []; // 清空用户列表
        this.selectedTransferUserId = null; // 重置移交选择

        // --- WebRTC Cleanup ---
        this.closeAllPeerConnections();
        this.peerConnections = {};
        this.dataChannels = {};
        // this.dataChannelOpenStatus = {};
        console.log('本地连接状态已清理');

        // 清空画布，确保退出房间或断开连接后画布变白
        if (this.ctx) {
           this.ctx.clearRect(0, 0, this.ctx.canvas.width, this.ctx.canvas.height);
           console.log('本地画布已清空');
        } else {
            console.warn('Canvas context (ctx) is not initialized, cannot clear canvas.');
        }
         this.updateUrlRoom(''); // 清空URL中的房间号
    },
    // 新增：更新URL中的房间号
    updateUrlRoom(room) {
        if (history.pushState) {
            const newUrl = new URL(window.location.href);
            if (room) {
                newUrl.searchParams.set('room', room);
            } else {
                newUrl.searchParams.delete('room');
            }
             // Check if origin matches and URL actually changed before pushing
             const targetOrigin = `${newUrl.protocol}//${newUrl.hostname}${newUrl.port ? ':' + newUrl.port : ''}`;
             if (window.location.origin === targetOrigin && newUrl.href !== window.location.href) {
               history.pushState({ path: newUrl.href }, '', newUrl.href);
            } else {
                console.log('URL update skipped: cross-origin or no change.');
            }
        }
    },
    startDrawing(e) {
       if (!this.currentRoom || !this.isConnected || this.myUserId === null) {
            console.warn('请先加入房间并确保 WebSocket 连接成功');
            return;
        }

        // --- Text Tool Logic ---
        if (this.currentTool === 'text') {
            this.handleTextInputClick(e); // Handle text input on click
            return; // Stop processing as drawing
        }
        // --- End Text Tool Logic ---


       // Ensure there's at least one data channel open to send data (for pen/eraser)
       const openDataChannels = Object.values(this.dataChannels).filter(dc => dc.readyState === 'open');
       if (openDataChannels.length === 0 && Object.keys(this.peerConnections).length > 0) {
           console.warn('没有打开的 WebRTC Data Channel，无法发送绘图数据。');
           // Still allow local drawing for responsiveness, just won't sync
       }


      this.isDrawing = true;
      const rect = this.ctx.canvas.getBoundingClientRect();
      this.lastX = e.clientX - rect.left;
      this.lastY = e.clientY - rect.top;

       // Set tool style for local drawing
       if (this.currentTool === 'pen') {
           this.ctx.strokeStyle = this.color;
           this.ctx.lineWidth = parseInt(this.lineWidth);
            this.ctx.globalCompositeOperation = 'source-over';
       } else if (this.currentTool === 'eraser') {
            this.ctx.strokeStyle = '#FFFFFF'; // 本地橡皮擦用白色画
            this.ctx.lineWidth = parseInt(this.lineWidth) + 5;
             this.ctx.globalCompositeOperation = 'destination-out'; // Local erase effect
       }
       this.ctx.lineCap = 'round';

       // Local immediate draw for responsiveness (draw a dot for pen/eraser start)
       // This is only needed if you want to see the very first point immediately.
       // It might create a tiny dot before mousemove fires.
       // Let's remove it for simplicity unless needed for specific feel.
       // this.ctx.beginPath();
       // this.ctx.moveTo(this.lastX, this.lastY);
       // this.ctx.lineTo(this.lastX + 0.1, this.lastY + 0.1);
       // this.ctx.stroke();

    },
    stopDrawing() {
      // stopDrawing is only relevant for line tools
      if (this.currentTool === 'pen' || this.currentTool === 'eraser') {
          if (!this.isDrawing) return;
          this.isDrawing = false;

           // Restore composite operation after drawing stops if using eraser
           if (this.currentTool === 'eraser') {
                this.ctx.globalCompositeOperation = 'source-over';
           }
      }
    },
    drawing(e) {
        // drawing is only relevant for line tools
      if (this.currentTool !== 'pen' && this.currentTool !== 'eraser') {
           return;
       }

      if (!this.isDrawing || !this.ctx) return;

      const rect = this.ctx.canvas.getBoundingClientRect();
      const currentX = e.clientX - rect.left;
      const currentY = e.clientY - rect.top;

      // Draw locally first
       // Styles are set in startDrawing for pen/eraser
      this.ctx.beginPath();
      this.ctx.moveTo(this.lastX, this.lastY);
      this.ctx.lineTo(currentX, currentY);
      this.ctx.stroke();


      // Send drawing data via WebRTC Data Channels
      // Now call the generic send method with type 'drawing' and payload
      this.sendWhiteboardData({
           type: 'drawing',
           payload: {
               tool: this.currentTool, // Send tool type
               color: this.color, // Send color (only relevant for pen)
               lineWidth: parseInt(this.lineWidth), // Send linewidth
               x1: this.lastX,
               y1: this.lastY,
               x2: currentX,
               y2: currentY
           }
       });

      // Update lastX and lastY
      this.lastX = currentX;
      this.lastY = currentY;
    },

    // --- New Method: Handle text input on canvas click ---
    handleTextInputClick(e) {
        if (!this.ctx) return;

        const rect = this.ctx.canvas.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        // Use prompt for simplified text input
        const text = prompt("请输入文本:");

        // If user entered text and didn't cancel
        if (text !== null && text.trim() !== '') {
             const textContent = text.trim(); // Use trimmed text

             // Prepare style object (using current color, default font/size)
             const style = {
                 color: this.color,
                 fontSize: this.fontSize, // Use data property
                 fontFamily: this.fontFamily // Use data property
             };

             // Add text to local canvas and send data
             this.addTextToCanvas(textContent, x, y, style);
        }
    },

    // --- New Method: Add text to local canvas and send ---
    addTextToCanvas(text, x, y, style) {
        if (!this.ctx) return;

        // Draw on local canvas
        this.ctx.save(); // Save current state
        this.ctx.fillStyle = style.color; // Set text color
        // Set font style: format is "font-size font-family"
        this.ctx.font = `${style.fontSize}px ${style.fontFamily}`;
         // Optional: set text baseline (e.g., 'top', 'middle', 'bottom')
         // 'bottom' is default for fillText, positioning y at the baseline.
         // If you want y to be the top of the text, use this.ctx.textBaseline = 'top';
         // Let's stick to default 'bottom' for now.

        this.ctx.fillText(text, x, y); // Draw the text
        this.ctx.restore(); // Restore state

        console.log(`在本地绘制文本: "${text}" at (${x}, ${y})`);

        // Send text data via Data Channels using the generic method
        this.sendWhiteboardData({
            type: 'text',
            payload: {
                text: text,
                x: x,
                y: y,
                style: style // Send style info
            }
        });
    },


    // --- WebRTC Methods ---

    // Update peer connections based on the current user list
    updatePeerConnections(currentUserIds) {
        if (this.myUserId === null) {
            console.warn("updatePeerConnections called before myUserId is set.");
            return;
        }

        const currentPeers = new Set(Object.keys(this.peerConnections).map(id => parseInt(id)));
        const activePeers = new Set([...currentUserIds].filter(id => id !== this.myUserId)); // Ensure activePeers is iterable


        // Identify peers to connect to (newly joined)
        const peersToConnect = [...activePeers].filter(id => !currentPeers.has(id));
        console.log("Peers to connect:", peersToConnect);

        // Identify peers to disconnect from (left)
        const peersToDisconnect = [...currentPeers].filter(id => !activePeers.has(id));
        console.log("Peers to disconnect:", peersToDisconnect);


        // Initiate connections for new peers
        for (const peerId of peersToConnect) {
            // For simplicity in full mesh, the peer with the lower ID initiates the connection (creates offer)
            // This prevents both sides from creating offers simultaneously, which can cause issues.
            if (this.myUserId < peerId) {
                 console.log(`Initiating WebRTC connection with user ${peerId} (my ID ${this.myUserId} < peer ID).`);
                 this.createPeerConnection(peerId, true); // true indicates 'initiator'
            } else {
                 console.log(`Waiting for WebRTC offer from user ${peerId} (my ID ${this.myUserId} > peer ID).`);
                 this.createPeerConnection(peerId, false); // false indicates 'receiver'
            }
        }

        // Close connections for departed peers
        for (const peerId of peersToDisconnect) {
             console.log(`Closing WebRTC connection with departed user ${peerId}.`);
             this.closePeerConnection(peerId);
        }
    },

    // Create a new RTCPeerConnection and setup handlers
    createPeerConnection(targetUserId, isInitiator) {
        if (this.peerConnections[targetUserId]) {
            console.warn(`PeerConnection with user ${targetUserId} already exists.`);
            return;
        }

        console.log(`Creating RTCPeerConnection for user ${targetUserId}.`);
        const pc = new RTCPeerConnection(rtcConfig);
        this.peerConnections[targetUserId] = pc;


        // ICE Candidate handler
        pc.onicecandidate = event => {
            if (event.candidate) {
                console.log(`发送 ICE Candidate 给用户 ${targetUserId}`);
                this.sendSignalingMessage(targetUserId, 'candidate', event.candidate);
            }
        };

        // ICE Connection State handler (optional, useful for debugging)
        pc.oniceconnectionstatechange = () => {
            console.log(`ICE connection state with user ${targetUserId}: ${pc.iceConnectionState}`);
            // You might update UI based on state (e.g., 'connected', 'disconnected')
        };

         // Connection State handler (WebRTC 1.0)
         pc.onconnectionstatechange = () => {
             console.log(`WebRTC connection state with user ${targetUserId}: ${pc.connectionState}`);
             if (pc.connectionState === 'disconnected' || pc.connectionState === 'failed' || pc.connectionState === 'closed') {
                 console.log(`Connection with user ${targetUserId} lost.`);
                 // This state change might happen before the user leaves the room list.
                 // The cleanup will primarily be triggered by user-list updates from WS.
             }
         };


        if (isInitiator) {
            // Initiator creates the Data Channel
            console.log(`创建 DataChannel "whiteboard" 给用户 ${targetUserId}`);
            const dataChannel = pc.createDataChannel("whiteboard");
            this.setupDataChannel(targetUserId, dataChannel);

            // Initiator creates Offer
            pc.createOffer()
                .then(offer => pc.setLocalDescription(offer))
                .then(() => {
                    console.log(`发送 SDP Offer 给用户 ${targetUserId}`);
                    this.sendSignalingMessage(targetUserId, 'offer', pc.localDescription);
                })
                .catch(error => console.error(`Error creating offer for user ${targetUserId}:`, error));

        } else {
            // Receiver listens for the Data Channel
            console.log(`监听 DataChannel "whiteboard" 从用户 ${targetUserId}`);
            pc.ondatachannel = event => {
                console.log(`收到 DataChannel 从用户 ${targetUserId}`);
                const dataChannel = event.channel;
                // Verify channel name if needed: if (dataChannel.label === "whiteboard")
                this.setupDataChannel(targetUserId, dataChannel);
            };
        }
    },

    // Setup Data Channel handlers
    setupDataChannel(targetUserId, dataChannel) {
         this.dataChannels[targetUserId] = dataChannel;

         dataChannel.onopen = () => {
             console.log(`DataChannel with user ${targetUserId} opened.`);
             // Optional: Send a message to confirm channel is ready
         };

         dataChannel.onmessage = event => {
             // console.log(`收到 DataChannel 消息 从用户 ${targetUserId}:`, event.data); // Too verbose
             try {
                 const data = JSON.parse(event.data);
                 // Handle different data types received via DataChannel
                 switch (data.type) {
                     case 'drawing':
                          // Ensure the drawing data has a payload
                         if (data.payload) {
                            this.drawOnCanvas(data.payload);
                         } else {
                             console.warn(`收到无效的绘图数据 payload 从用户 ${targetUserId}`);
                         }
                         break;
                     case 'text':
                          // Ensure the text data has a payload
                         if (data.payload) {
                            this.receiveTextFromPeer(data.payload);
                         } else {
                            console.warn(`收到无效的文本数据 payload 从用户 ${targetUserId}`);
                         }
                         break;
                     default:
                         console.warn(`收到未知类型的 DataChannel 消息 从用户 ${targetUserId}: ${data.type}`, data);
                 }

             } catch (error) {
                 console.error(`解析 DataChannel 消息失败 从用户 ${targetUserId}:`, error, event.data);
             }
         };

         dataChannel.onclose = () => {
             console.log(`DataChannel with user ${targetUserId} closed.`);
             // Data channel closing might indicate connection issue, but don't remove PC yet.
             // The PC's connectionstatechange or user-list update will handle full cleanup.
         };

         dataChannel.onerror = error => {
             console.error(`DataChannel with user ${targetUserId} error:`, error);
         };
    },

    // Close a specific peer connection
    closePeerConnection(targetUserId) {
        const pc = this.peerConnections[targetUserId];
        if (pc) {
            console.log(`Closing PeerConnection and DataChannel for user ${targetUserId}.`);
             // Close Data Channel first if it exists
            const dc = this.dataChannels[targetUserId];
            if (dc && dc.readyState !== 'closed') {
                 dc.close();
            }
             // Close Peer Connection
            if (pc.connectionState !== 'closed') {
                 pc.close();
            }

             // Remove from our lists
             delete this.peerConnections[targetUserId];
             delete this.dataChannels[targetUserId];
             // delete this.dataChannelOpenStatus[targetUserId];

        } else {
            console.warn(`Attempted to close non-existent PeerConnection for user ${targetUserId}.`);
        }
    },

    // Close all active peer connections
    closeAllPeerConnections() {
        console.log("Closing all peer connections.");
        // Iterate over a copy of keys because closing might modify the object
        Object.keys(this.peerConnections).forEach(userId => {
            this.closePeerConnection(parseInt(userId)); // Ensure userId is number if keys are strings
        });
    },


    // Send signaling message via WebSocket
    sendSignalingMessage(targetUserId, type, payload) {
        if (!this.websocket || this.websocket.readyState !== WebSocket.OPEN) {
            console.warn(`WebSocket not open, cannot send signaling message ${type} to ${targetUserId}.`);
            return;
        }
         if (this.myUserId === null) {
             console.warn("myUserId not set, cannot send signaling message.");
             return;
         }

        const message = {
            type: type,
            room: this.currentRoom,
            userId: this.myUserId, // Sender ID (redundant but good practice)
            targetUserId: targetUserId, // Recipient ID
            data: payload // SDP or ICE candidate
        };
        this.websocket.send(JSON.stringify(message));
    },

    // Handle incoming signaling messages from WebSocket
    async handleSignalingMessage(senderUserId, type, payload) {
        // Ensure we have a peer connection for this sender
        // If we receive an offer, we might need to create the PC first
        // If we receive answer/candidate, the PC should already exist (initiated by us)
        let pc = this.peerConnections[senderUserId];

        if (!pc && type === 'offer') {
             // If receiving an offer, create a new peer connection for this sender
             console.log(`收到 Offer 从用户 ${senderUserId}, 创建 RTCPeerConnection (作为接收者).`);
             this.createPeerConnection(senderUserId, false); // Not initiator
             pc = this.peerConnections[senderUserId];
             if (!pc) {
                 console.error(`Failed to create PeerConnection for user ${senderUserId} upon receiving offer.`);
                 return;
             }
        } else if (!pc) {
             console.warn(`收到信令消息 (${type}) 从用户 ${senderUserId}, 但没有对应的 PeerConnection。忽略。`);
             return;
        }


        try {
            if (type === 'offer') {
                await pc.setRemoteDescription(new RTCSessionDescription(payload));
                const answer = await pc.createAnswer();
                await pc.setLocalDescription(answer);
                console.log(`发送 SDP Answer 给用户 ${senderUserId}`);
                this.sendSignalingMessage(senderUserId, 'answer', pc.localDescription);

            } else if (type === 'answer') {
                await pc.setRemoteDescription(new RTCSessionDescription(payload));

            } else if (type === 'candidate') {
                 if (payload) { // Ensure candidate is not null
                    await pc.addIceCandidate(new RTCIceCandidate(payload));
                    console.log(`添加 ICE Candidate 从用户 ${senderUserId}`);
                 } else {
                     console.log(`收到 null ICE candidate 从用户 ${senderUserId}, 表示 ICE 收集完成。`);
                 }
            }
        } catch (error) {
            console.error(`处理信令消息 (${type}) 从用户 ${senderUserId} 失败:`, error);
            // Depending on the error, you might want to close the connection with this peer
            // this.closePeerConnection(senderUserId);
        }
    },

    // --- Modified: Send generic whiteboard data (drawing, text, etc.) via all open Data Channels ---
    sendWhiteboardData(dataObject) {
         if (!this.myUserId) {
              console.warn('myUserId not set, cannot send whiteboard data.');
              return;
         }
         if (!dataObject || !dataObject.type || !dataObject.payload) {
             console.warn('Invalid data object provided to sendWhiteboardData.');
             return;
         }

        const messageString = JSON.stringify(dataObject);

        
        for (const targetUserId in this.dataChannels) {
             const dc = this.dataChannels[targetUserId];
             if (dc && dc.readyState === 'open') {
                 try {
                     dc.send(messageString);
                     
                 } catch (error) {
                     console.error(`Error sending ${dataObject.type} data via DataChannel to user ${targetUserId}:`, error);
                     // The channel might be closing or have an issue, its error/close handlers should trigger
                 }
             }
        }
         // console.log(`发送 ${dataObject.type} 数据到 ${sentCount} 个DataChannel`); // Optional: log send count
    },


    // --- Modified: Draw on canvas using received drawing payload ---
    drawOnCanvas(drawingPayload) {
      if (!this.ctx || !drawingPayload || drawingPayload.x1 === undefined) return;

       // IMPORTANT: Received drawing data should be from *other* users.
       // This function applies the drawing using the received style, not the local one.

      this.ctx.save(); // Save current Canvas state

      // Set style and composite operation based on received data
      // For multi-user, it's usually safer to use 'source-over' for both pen and eraser
      // Using 'destination-out' remotely can be complex to sync across multiple clients.
      if (drawingPayload.tool === 'pen') {
          this.ctx.strokeStyle = drawingPayload.color;
          this.ctx.lineWidth = drawingPayload.lineWidth;
          this.ctx.globalCompositeOperation = 'source-over'; // Use source-over for remote pen
      } else if (drawingPayload.tool === 'eraser') {
           // Use white for remote eraser drawing over the existing content
           this.ctx.strokeStyle = '#FFFFFF';
           this.ctx.lineWidth = parseInt(drawingPayload.lineWidth) + 5; // Use received eraser size
           this.ctx.globalCompositeOperation = 'source-over'; // Use source-over for remote eraser (draw white)
      }
      this.ctx.lineCap = 'round';

      this.ctx.beginPath();
      this.ctx.moveTo(drawingPayload.x1, drawingPayload.y1);
      this.ctx.lineTo(drawingPayload.x2, drawingPayload.y2);
      this.ctx.stroke();

      this.ctx.restore(); // Restore Canvas state
    },

    // --- New Method: Receive and draw text from a peer ---
    receiveTextFromPeer(textPayload) {
         if (!this.ctx || !textPayload || textPayload.text === undefined) return;

         console.log(`收到并绘制文本: "${textPayload.text}" at (${textPayload.x}, ${textPayload.y})`);

         this.ctx.save(); // Save current state
         this.ctx.fillStyle = textPayload.style.color; // Set text color from payload
         // Set font style from payload
         this.ctx.font = `${textPayload.style.fontSize}px ${textPayload.style.fontFamily}`;
         // Optional: set text baseline if sender set it differently
         // e.g., this.ctx.textBaseline = 'top'; // If sender used 'top'

         this.ctx.fillText(textPayload.text, textPayload.x, textPayload.y); // Draw the text
         this.ctx.restore(); // Restore state
    },


    clearCanvas() {
        // Client permission check (still via WS for host command)
        if (this.isClearDisabled) {
            console.log("您不是主机，无法清空画布");
            alert("只有主机可以清空画布"); // 友好的提示
            return;
        }

        if (!this.currentRoom || !this.isConnected || this.myUserId === null) {
           alert('请先加入房间并确保 WebSocket 连接成功');
           return;
        }

        // Clear local Canvas immediately
        if (this.ctx) {
          this.ctx.clearRect(0, 0, this.ctx.canvas.width, this.ctx.canvas.height);
          console.log('本地立即清空画布');
        }


        // Send clear command to server via WebSocket
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            const clearMessage = JSON.stringify({
                type: 'clear',
                room: this.currentRoom,
                userId: this.myUserId // Server checks if sender is host
            });
            this.websocket.send(clearMessage);
            console.log('发送清空白板指令 (作为主机) via WS');
        } else {
             console.warn('WebSocket 未连接或未打开，无法发送清空指令');
        }
    },

    // Send transfer host message via WebSocket
      transferHost() {
          if (!this.isHost) {
              alert("您不是主机，无法移交权限。");
              return;
          }
          if (this.selectedTransferUserId === null || this.selectedTransferUserId === '') {
               alert("请选择一个用户进行移交。");
               return;
          }
          // Optional: Check if target user is in the active user list and has a connected data channel?
          // For now, rely on server-side check + user list being up-to-date.
           if (!this.users.some(user => user.id === this.selectedTransferUserId)) {
               alert("选中的用户不存在或已离开。");
               this.selectedTransferUserId = null; // 重置选择
               return;
           }


          if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
              const transferMessage = JSON.stringify({
                  type: 'transfer-host',
                  room: this.currentRoom,
                  userId: this.myUserId, // Sender ID
                  targetUserId: this.selectedTransferUserId // Recipient ID
              });
              this.websocket.send(transferMessage);
              console.log(`发送移交主机消息到用户 ID: ${this.selectedTransferUserId} via WS`);

              // --- ADDED: Immediately update local state for UI responsiveness ---
              // Assume transfer is successful client-side for immediate UI update
              // Server will confirm via user-list update, but this prevents race conditions on UI state
              this.isHost = false;
              // Also reset selected user in the dropdown after sending
              this.selectedTransferUserId = null;
              // --- END ADDED ---

          } else {
              console.warn('WebSocket 未连接或未打开，无法发送移交主机消息');
          }
      }
  },
  beforeUnmount() {
       // In component destruction, try to send leave message and close connection
      if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
           try {
               const leaveMessage = JSON.stringify({
                    type: 'leave',
                    room: this.currentRoom,
                    userId: this.myUserId
                });
               this.websocket.send(leaveMessage);
               console.log('组件销毁前发送退出房间消息');
           } catch (e) {
               console.warn('发送退出消息失败:', e);
           } finally {
               // Close WebSocket, which triggers onclose and cleanupConnection
               this.websocket.close();
               console.log('组件销毁前关闭WebSocket连接');
           }
      } else {
          // If WS is not open, just perform local cleanup
          this.cleanupConnection();
      }
  }
}
</script>

<style scoped>
/* ... (Your existing styles) ... */

/* Added style for user list connection status */
.user-management li span {
    font-weight: normal; /* Reset font-weight for status text */
    margin-left: 5px;
    color: gray; /* Default color */
}

.user-management li span[v-if="getUserConnectionStatus(user.id)"] {
     color: green; /* Color for connected status */
}

/* Ensure the base styles are still included */
#app {
  max-width: 960px; margin: 40px auto; padding: 20px; background-color: #ffffff; border-radius: 12px; box-shadow: 0 8px 16px rgba(0, 0, 0, 0.1); text-align: center; color: #333; display: flex; flex-direction: column; align-items: center; gap: 20px;
}
#app h1 { color: #0056b3; margin-bottom: 20px; }
#app > div.control-area {
    width: 100%; padding: 15px; background-color: #e9ecef; border-radius: 8px; box-shadow: 0 2px 5px rgba(0, 0, 0, 0.08); display: flex; align-items: center; gap: 15px; flex-wrap: wrap; justify-content: center;
}

#app input[type="text"], #app input[type="color"], #app input[type="range"],
#app button, #app select {
  padding: 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 1em; vertical-align: middle;
  box-sizing: border-box;
}

#app label {
    font-weight: bold; color: #555; margin-right: 5px;
}
#app button {
  border: none; background-color: #007bff; color: white; cursor: pointer; transition: background-color 0.2s ease, opacity 0.2s ease;
}
#app button:hover:not(:disabled) {
  background-color: #0056b3;
}
#app button:disabled {
  background-color: #ccc; cursor: not-allowed; opacity: 0.6;
}

#app > div.control-area:first-of-type > span {
    font-size: 1em; color: #28a745; margin-left: 10px; font-weight: bold;
}
#app .control-area span {
    margin-left: 0; /* Override potential margin from the first span rule */
    min-width: 20px;
    text-align: center;
    color: #333;
    font-weight: normal;
}
#whiteboard {
  border: 1px solid #ccc; border-radius: 8px; box-shadow: 0 4px 8px rgba(0, 0, 0, 0.1); cursor: crosshair; background-color: white;
}
button.active {
  background-color: #28a745; color: white;
}
button.active:hover:not(:disabled) {
  background-color: #218838;
}
#app > div.control-area:first-of-type input[type="text"] {
    flex-grow: 1; max-width: 150px;
}
#app input[type="color"] {
    padding: 4px; height: 32px;
}
#app input[type="range"] {
    flex-grow: 1; max-width: 150px; padding: 0;
    height: 32px;
}

.user-management {
    flex-direction: column;
    align-items: flex-start;
    gap: 10px;
}

.user-management h3 {
    margin: 0 0 5px 0;
    width: 100%;
    text-align: center;
    color: #0056b3;
}

.user-management ul {
    list-style: none;
    padding: 0;
    margin: 0;
    width: 100%;
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    justify-content: center;
}

.user-management li {
    background-color: #e9ecef;
    border: 1px solid #ced4da;
    border-radius: 4px;
    padding: 5px 10px;
    font-size: 0.9em;
    color: #495057;
     min-width: 80px;
     text-align: center;
     display: flex;
     align-items: center;
     justify-content: center;
     gap: 5px;
}

.user-management li.is-host {
    background-color: #ffc107;
    border-color: #ffA000;
    font-weight: bold;
    color: #333;
}

.user-management li.is-me {
    border-color: #007bff;
    box-shadow: 0 0 5px rgba(0, 123, 255, 0.3);
}

.host-transfer {
    width: 100%;
    padding-top: 10px;
    border-top: 1px solid #ced4da;
    display: flex;
    align-items: center;
    gap: 10px;
    justify-content: center;
    flex-wrap: wrap;
    font-size: 0.95em;
}

.host-transfer h4 {
    margin: 0;
    color: #007bff;
    font-size: 1em;
}
.host-transfer p {
    margin: 0;
    color: #555;
    font-style: italic;
}


.host-transfer select {
    padding: 8px;
    border: 1px solid #ccc;
    border-radius: 4px;
    font-size: 1em;
}

.host-transfer button {
    background-color: #ffc107;
}
.host-transfer button:hover:not(:disabled) {
    background-color: #ffA000;
}

</style>
