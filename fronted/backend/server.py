# server.py (修改版)
import asyncio
import websockets
import json
import logging
import itertools

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Simple counter for unique user IDs across all connections
user_id_counter = itertools.count(1)

# 用于存储连接的客户端，按照房间号分组
# 结构示例: { "room1": { 1: {websocket: ws1, isHost: True}, 2: {websocket: ws2, isHost: False} }, ... }
# 修改为字典，方便通过 user_id 查找 websocket
connected_clients = {}

# 用于存储每个房间的主机 user_id
# 结构示例: { "room1": 1, "room2": 3 }
# None 表示房间当前没有主机
room_hosts = {}


def get_user_info_by_websocket(websocket):
    """根据 websocket 查找用户信息 (房间号, 用户ID, 用户信息字典)"""
    # 迭代所有房间
    for room_id, users in connected_clients.items():
        # 迭代房间内的所有用户
        for user_id, user_info in users.items():
            if user_info['websocket'] == websocket:
                return room_id, user_id, user_info
    return None, None, None # Not found

def get_user_websocket_by_id(room_id, user_id):
    """根据房间号和用户ID查找 websocket"""
    if room_id in connected_clients and user_id in connected_clients[room_id]:
        return connected_clients[room_id][user_id]['websocket']
    return None


async def send_to_user(room_id, target_user_id, message_dict):
    """向房间内的指定客户端发送消息"""
    target_websocket = get_user_websocket_by_id(room_id, target_user_id)
    if target_websocket and target_websocket.state == websockets.protocol.State.OPEN:
        try:
            message_str = json.dumps(message_dict)
            await target_websocket.send(message_str)
            # logging.debug(f"Sent message type {message_dict.get('type')} to user {target_user_id} in room {room_id}")
        except Exception as e:
            logging.error(f"Error sending message to user {target_user_id} in room {room_id}: {e}")
            # Consider disconnecting the user if sending fails
    else:
        logging.warning(f"Attempted to send message to non-existent or closed user {target_user_id} in room {room_id}.")


async def broadcast_user_list(room_id):
    """向房间内的所有客户端广播最新的用户列表"""
    if room_id in connected_clients and connected_clients[room_id]:
        # 构建要广播的用户列表，只包含ID和isHost信息
        user_list_data = []
        current_host_id = room_hosts.get(room_id)
        for user_id, user_info in connected_clients[room_id].items():
             # 只广播活跃的用户
             if user_info.get('active', True): # assuming 'active' flag might be added later
                user_list_data.append({
                    'id': user_id,
                    'isHost': user_id == current_host_id
                })

        message = {
            'type': 'user-list',
            'room': room_id,
            'users': user_list_data
        }
        message_str = json.dumps(message)

        # 向房间内的所有客户端发送消息
        clients_ws = [user_info['websocket'] for user_id, user_info in connected_clients[room_id].items() if user_info['websocket'].state == websockets.protocol.State.OPEN]
        if clients_ws:
            await asyncio.gather(*[client.send(message_str) for client in clients_ws])
            logging.debug(f"Broadcasted user list to {len(clients_ws)} clients in room {room_id}")
        else:
             logging.debug(f"No clients in room {room_id} to broadcast user list to.")


async def register(websocket, room_id):
    """将新的 WebSocket 连接注册到指定房间，并处理主机分配和用户列表广播"""
    # Note: User ID hint from client is not used in this server version, always assigns new ID
    user_id = next(user_id_counter)
    is_host = False

    if room_id not in connected_clients:
        connected_clients[room_id] = {} # Change from list to dict
        logging.info(f"Room '{room_id}' created.")

    # 检查房间是否有主机，如果没有，则将当前连接设为主机
    # 或者房间里没有其他用户了，也可以成为主机
    if room_id not in room_hosts or room_hosts[room_id] is None or not connected_clients[room_id]:
        room_hosts[room_id] = user_id
        is_host = True
        logging.info(f"Client {websocket.remote_address} (ID: {user_id}) assigned as host for room '{room_id}'.")
    else:
         logging.info(f"Client {websocket.remote_address} (ID: {user_id}) joins room '{room_id}' as guest.")

    # 创建用户对象并添加到房间字典
    user_info = {'websocket': websocket, 'isHost': is_host} # ID is the key in the dict
    connected_clients[room_id][user_id] = user_info

    logging.info(f"Client {websocket.remote_address} (ID: {user_id}) registered to room '{room_id}'. Current clients: {len(connected_clients[room_id])}")

    # 向新加入的客户端发送确认消息，包含其ID和是否是主机的信息
    await websocket.send(json.dumps({
        'type': 'joined',
        'room': room_id,
        'userId': user_id, # 发送自己的ID
        'isHost': is_host, # 告知客户端它是不是主机
        'message': 'Successfully joined room'
    }))

    # 广播更新后的用户列表给所有客户端
    await broadcast_user_list(room_id)


async def unregister(websocket):
    """从其所在房间注销 WebSocket 连接，处理主机离开和用户列表广播"""
    # Find user info using the websocket
    room_id, user_id, user_info = get_user_info_by_websocket(websocket)

    if user_info:
        # Remove user from the room dictionary
        if user_id in connected_clients[room_id]:
            del connected_clients[room_id][user_id]
            logging.info(f"Client {websocket.remote_address} (ID: {user_id}) unregistered from room '{room_id}'. Remaining clients: {len(connected_clients[room_id])}")

        # If the leaving client was the host
        if room_hosts.get(room_id) == user_id:
            # Host left, set room host to None
            room_hosts[room_id] = None
            logging.info(f"Host {websocket.remote_address} (ID: {user_id}) left room '{room_id}'. Room now has no host.")
            # TODO: Optionally implement complex host handoff here

        # If the room is empty, remove the room and its host record
        if not connected_clients[room_id]:
            del connected_clients[room_id]
            logging.info(f"Room '{room_id}' is now empty and removed.")
            if room_id in room_hosts:
                 del room_hosts[room_id]
                 logging.info(f"Host record for room '{room_id}' removed.")
        else:
            # If the room is not empty, broadcast the updated user list
            await broadcast_user_list(room_id)

    else:
        logging.warning(f"Attempted to unregister unknown websocket: {websocket.remote_address}")


async def handler(websocket):
    """处理单个 WebSocket 连接"""
    room_id = None
    user_id = None # Store user_id here for easy access in the loop
    try:
        # 客户端连接后，第一个消息必须是加入房间的请求
        message = await websocket.recv()
        data = json.loads(message)

        if data.get('type') == 'join':
            room_id = data.get('room')
            if not room_id:
                await websocket.send(json.dumps({'type': 'error', 'message': 'Room ID is required'}))
                logging.warning(f"Client {websocket.remote_address} sent join message without room ID.")
                return # Exit handler if no room_id

            # Execute registration logic
            await register(websocket, room_id)
            # After successful registration, get the assigned user_id
            _, assigned_user_id, _ = get_user_info_by_websocket(websocket) # Use the get function again
            if assigned_user_id is not None:
                user_id = assigned_user_id
                logging.info(f"Handler bound to user_id {user_id} for websocket {websocket.remote_address} in room {room_id}")
            else:
                 logging.error(f"Failed to get user_info after registration for {websocket.remote_address}. Disconnecting.")
                 await websocket.send(json.dumps({'type': 'error', 'message': 'Internal server error during join.'}))
                 return # Exit the handler if registration failed

            # Now that the user is registered and we have user_id/room_id,
            # start the message receiving loop
            async for message in websocket:
                try:
                    data = json.loads(message)
                    message_type = data.get('type')
                    # Ensure message includes the room and sender user ID (redundant as server knows, but good practice)
                    # sender_user_id = data.get('userId') # Not strictly needed, server knows who sent it

                    # Basic validation: message should be from the expected room
                    if data.get('room') != room_id:
                         logging.warning(f"Received message for wrong room from ID:{user_id} ({websocket.remote_address}). Expected '{room_id}', got '{data.get('room')}'.")
                         await websocket.send(json.dumps({'type': 'error', 'message': f"Message sent for wrong room {data.get('room')}."}))
                         continue

                    # Get current host status (re-check for each message as it can change)
                    current_host_id = room_hosts.get(room_id)
                    is_host = (user_id is not None) and (user_id == current_host_id)


                    if message_type == 'clear':
                         # Clear command is still handled by the server and broadcast via WS
                         if is_host:
                             # Broadcast clear command to all others in the room via WS
                             # Client receiving 'clear' will clear its canvas.
                             clear_message = {'type': 'clear', 'room': room_id, 'userId': user_id}
                             clients_ws = [user_info['websocket'] for other_user_id, user_info in connected_clients[room_id].items() if other_user_id != user_id and user_info['websocket'].state == websockets.protocol.State.OPEN]
                             if clients_ws:
                                 await asyncio.gather(*[client.send(json.dumps(clear_message)) for client in clients_ws])
                             logging.info(f"Host ID:{user_id} broadcasted clear canvas command in room {room_id}")
                         else:
                             logging.warning(f"Client ID:{user_id} (not host) attempted to send clear command in room {room_id}.")
                             await websocket.send(json.dumps({'type': 'error', 'message': 'Only the host can clear the canvas.'}))

                    elif message_type == 'leave':
                         logging.info(f"Client ID:{user_id} ({websocket.remote_address}) sent leave message for room {room_id}. Closing connection.")
                         # Closing the websocket will trigger the finally block for unregistration
                         await websocket.close()
                         # The loop will exit automatically

                    elif message_type == 'transfer-host':
                         target_user_id = data.get('targetUserId')
                         if is_host:
                             if target_user_id is None:
                                  logging.warning(f"Host ID:{user_id} in room {room_id} sent transfer-host without targetUserId.")
                                  await websocket.send(json.dumps({'type': 'error', 'message': 'Transfer host requires a target user ID.'}))
                                  continue

                             target_user_ws = get_user_websocket_by_id(room_id, target_user_id)

                             if target_user_ws and target_user_ws.state == websockets.protocol.State.OPEN:
                                 if target_user_id == user_id:
                                      logging.warning(f"Host ID:{user_id} attempted to transfer host to self in room {room_id}.")
                                      await websocket.send(json.dumps({'type': 'error', 'message': 'Cannot transfer host to yourself.'}))
                                 else:
                                     # Successful transfer
                                     room_hosts[room_id] = target_user_id
                                     logging.info(f"Host ID:{user_id} successfully transferred host to ID:{target_user_id} in room {room_id}.")
                                     # Broadcast updated user list to reflect new host
                                     await broadcast_user_list(room_id)
                                     # Optional: send confirmation messages via WS
                                     await websocket.send(json.dumps({'type': 'info', 'message': f'You have transferred host role to User {target_user_id}.'}))
                                     await send_to_user(room_id, target_user_id, {'type': 'info', 'message': f'You have been assigned the host role for room {room_id}.'})

                             else:
                                 logging.warning(f"Host ID:{user_id} in room {room_id} attempted to transfer host to non-existent or offline user ID:{target_user_id}.")
                                 await websocket.send(json.dumps({'type': 'error', 'message': f'Target user ID {target_user_id} not found or is offline.'}))

                         else:
                              logging.warning(f"Client ID:{user_id} (not host) attempted to send transfer-host command in room {room_id}.")
                              await websocket.send(json.dumps({'type': 'error', 'message': 'Only the host can transfer the host role.'}))

                    # --- WebRTC Signaling Messages ---
                    elif message_type in ['offer', 'answer', 'candidate']:
                        # These messages must include a targetUserId to know who to send them to
                        target_user_id = data.get('targetUserId')
                        if target_user_id is None:
                            logging.warning(f"Received WebRTC signal ({message_type}) from ID:{user_id} in room {room_id} without targetUserId.")
                            await websocket.send(json.dumps({'type': 'error', 'message': f'WebRTC signal {message_type} requires targetUserId.'}))
                            continue

                        # Forward the signaling message to the target user
                        # Ensure the sender's ID is included so the recipient knows who it's from
                        signal_message = {
                            'type': message_type,
                            'room': room_id,
                            'senderUserId': user_id, # Add sender ID
                            'data': data.get('data') # The actual SDP or candidate payload
                        }
                        await send_to_user(room_id, target_user_id, signal_message)
                        logging.debug(f"Forwarded WebRTC signal ({message_type}) from ID:{user_id} to ID:{target_user_id} in room {room_id}")

                    # Drawing messages are now expected via Data Channel, not WebSocket
                    elif message_type == 'drawing':
                        logging.warning(f"Received 'drawing' message via WebSocket from ID:{user_id} in room {room_id}. Expected Data Channel.")
                        # Optionally send error back, but for simplicity, just log and ignore.

                    else:
                        logging.warning(f"Received unknown message type '{message_type}' from ID:{user_id} ({websocket.remote_address}) in room {room_id}: {data}")
                        await websocket.send(json.dumps({'type': 'error', 'message': f"Unknown message type {message_type}."}))


                except json.JSONDecodeError:
                    logging.error(f"Received invalid JSON from ID:{user_id} ({websocket.remote_address}) in room {room_id}")
                    await websocket.send(json.dumps({'type': 'error', 'message': 'Invalid JSON format.'}))
                except Exception as e:
                    logging.error(f"Error processing message from ID:{user_id} ({websocket.remote_address}) in room {room_id}: {e}", exc_info=True)
                    # Don't necessarily close connection on every message error, but report it
                    await websocket.send(json.dumps({'type': 'error', 'message': f'Server error processing your message: {e}'}))


        else:
            # If the first message wasn't 'join'
            await websocket.send(json.dumps({'type': 'error', 'message': 'First message must be "join"'}))
            logging.warning(f"Client {websocket.remote_address} first message was not 'join': {data}")

    except websockets.exceptions.ConnectionClosedOK:
        logging.info(f"Client ID:{user_id if user_id is not None else 'N/A'} disconnected gracefully.")
    except websockets.exceptions.ConnectionClosedError as e:
        logging.error(f"Client ID:{user_id if user_id is not None else 'N/A'} disconnected with error: {e}")
    except Exception as e:
        # This catches errors that happen before or during join/registration
        logging.error(f"An unexpected error occurred with client {websocket.remote_address} (likely before message loop): {e}", exc_info=True)
        # Attempt to send an error message if the websocket is still open
        if websocket.state == websockets.protocol.State.OPEN:
            try:
                await websocket.send(json.dumps({'type': 'error', 'message': f'Server experienced an unexpected error: {e}'}))
            except Exception:
                pass # Ignore errors sending error messages
    finally:
        # Always attempt to unregister when the handler exits (connection closes)
        if room_id and user_id is not None:
             await unregister(websocket) # unregister finds info by websocket
             logging.info(f"Handler cleanup completed for ID:{user_id} in room {room_id}.")
        else:
             # If room_id or user_id was not successfully obtained, the client wasn't fully registered.
             logging.info(f"Handler cleanup for unregistered client {websocket.remote_address}.")
             # No need to call unregister as they weren't added to connected_clients


async def main():
    # Using a simple STUN server (optional but recommended for WebRTC)
    # You might need to install: pip install websockets aiohttp
    # For a real-world app, use a more robust STUN/TURN server
    stun_server = "stun:stun.l.google.com:19302" # Public Google STUN server

    # Note: STUN server configuration is handled on the client side (in RTCPeerConnection config)
    # The server just needs to run the websocket for signaling.

    server = await websockets.serve(handler, "0.0.0.0", 8765)
    logging.info("WebSocket signaling server started on ws://0.0.0.0:8765")
    logging.info(f"WebRTC STUN server to potentially use on client: {stun_server}")
    await server.wait_closed()

if __name__ == "__main__":
    try:
        # For asyncio.run() in Python 3.7+, replace with loop management for older versions
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("Server stopped by user.")
    except Exception as e:
        logging.critical(f"Server failed to start or encountered fatal error: {e}", exc_info=True)

