import streamlit as st
import json
import requests
import websocket
import threading
import uuid
import time
import pandas as pd
from typing import Dict, List, Optional, Any, Union
import logging
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO, 
                   format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("n8n-mcp-client")

# Page configuration
st.set_page_config(
    page_title="n8n MCP Client", 
    page_icon="⚙️", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS
st.markdown("""
<style>
    .main-header {
        font-size: 2.5rem;
        font-weight: 700;
        margin-bottom: 1rem;
    }
    .sub-header {
        font-size: 1.5rem;
        font-weight: 600;
        margin-bottom: 1rem;
    }
    .card {
        padding: 1.5rem;
        border-radius: 0.5rem;
        background-color: #f8f9fa;
        margin-bottom: 1rem;
        border: 1px solid #dee2e6;
    }
    .success-msg {
        padding: 0.75rem;
        border-radius: 0.25rem;
        background-color: #d4edda;
        color: #155724;
        margin-bottom: 1rem;
    }
    .error-msg {
        padding: 0.75rem;
        border-radius: 0.25rem;
        background-color: #f8d7da;
        color: #721c24;
        margin-bottom: 1rem;
    }
    .info-msg {
        padding: 0.75rem;
        border-radius: 0.25rem;
        background-color: #d1ecf1;
        color: #0c5460;
        margin-bottom: 1rem;
    }
    .workflow-item {
        padding: 1rem;
        border-radius: 0.25rem;
        background-color: #f8f9fa;
        margin-bottom: 0.5rem;
        border: 1px solid #dee2e6;
    }
    .param-input {
        margin-bottom: 0.5rem;
    }
    .history-item {
        padding: 0.5rem;
        border-radius: 0.25rem;
        background-color: #f8f9fa;
        margin-bottom: 0.5rem;
        border: 1px solid #dee2e6;
    }
</style>
""", unsafe_allow_html=True)

# Initialize session state
if 'workflows' not in st.session_state:
    st.session_state.workflows = []
if 'history' not in st.session_state:
    st.session_state.history = []
if 'connection_status' not in st.session_state:
    st.session_state.connection_status = "Disconnected"
if 'selected_workflow' not in st.session_state:
    st.session_state.selected_workflow = None
if 'workflow_parameters' not in st.session_state:
    st.session_state.workflow_parameters = {}

# Sidebar for credentials and connection settings
st.sidebar.markdown("<div class='sub-header'>Connection Settings</div>", unsafe_allow_html=True)

# Connection credentials in sidebar
connection_type = st.sidebar.radio("Connection Type", ["HTTP", "WebSocket"])
server_url = st.sidebar.text_input("n8n MCP Server URL", "http://localhost:5678/api/mcp")
api_key = st.sidebar.text_input("API Key (if required)", type="password")

# Advanced settings collapsible section
with st.sidebar.expander("Advanced Settings"):
    timeout = st.number_input("Timeout (seconds)", min_value=1, value=30)
    max_retries = st.number_input("Max Retries", min_value=0, value=3)
    retry_delay = st.number_input("Retry Delay (seconds)", min_value=0, value=1)
    log_level = st.selectbox("Log Level", ["DEBUG", "INFO", "WARNING", "ERROR"])
    if log_level:
        logger.setLevel(getattr(logging, log_level))

# MCP Client implementation
class MCPClient:
    def __init__(self, server_url: str, api_key: Optional[str] = None, 
                 connection_type: str = "HTTP", timeout: int = 30,
                 max_retries: int = 3, retry_delay: int = 1):
        self.server_url = server_url
        self.api_key = api_key
        self.connection_type = connection_type
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.ws = None
        self.ws_connected = False
        self.message_callbacks = {}
        
    def get_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json"
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers
    
    def connect_websocket(self):
        if self.connection_type != "WebSocket":
            return
            
        ws_url = self.server_url
        if ws_url.startswith("http://"):
            ws_url = ws_url.replace("http://", "ws://")
        elif ws_url.startswith("https://"):
            ws_url = ws_url.replace("https://", "wss://")
            
        def on_message(ws, message):
            try:
                logger.debug(f"WebSocket message received: {message}")
                data = json.loads(message)
                message_id = data.get("id")
                if message_id in self.message_callbacks:
                    callback = self.message_callbacks[message_id]
                    callback(data)
                    del self.message_callbacks[message_id]
            except Exception as e:
                logger.error(f"Error processing message: {e}")
                
        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
            self.ws_connected = False
            st.session_state.connection_status = "Error"
            
        def on_close(ws, close_status_code, close_msg):
            logger.warning(f"WebSocket connection closed: {close_status_code} - {close_msg}")
            self.ws_connected = False
            st.session_state.connection_status = "Disconnected"
            
        def on_open(ws):
            logger.info("WebSocket connected")
            self.ws_connected = True
            st.session_state.connection_status = "Connected"
            
        self.ws = websocket.WebSocketApp(
            ws_url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            header=self.get_headers()
        )
        
        wst = threading.Thread(target=self.ws.run_forever)
        wst.daemon = True
        wst.start()
    
    def send_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if not message.get("id"):
            message["id"] = str(uuid.uuid4())
            
        logger.debug(f"Sending message: {message}")
        
        if self.connection_type == "WebSocket":
            if not self.ws_connected:
                self.connect_websocket()
                # Wait for connection
                for _ in range(10):
                    if self.ws_connected:
                        break
                    time.sleep(0.5)
                    
            if not self.ws_connected:
                logger.error("Failed to connect to WebSocket")
                return {"error": "Failed to connect to WebSocket"}
                
            result = {}
            event = threading.Event()
            
            def callback(data):
                nonlocal result
                result = data
                event.set()
                
            self.message_callbacks[message["id"]] = callback
            self.ws.send(json.dumps(message))
            
            if not event.wait(timeout=self.timeout):
                logger.error("Request timed out")
                return {"error": "Request timed out"}
                
            return result
        else:
            # HTTP connection with retries
            for attempt in range(self.max_retries + 1):
                try:
                    logger.debug(f"HTTP request attempt {attempt+1}/{self.max_retries+1}")
                    response = requests.post(
                        self.server_url,
                        headers=self.get_headers(),
                        json=message,
                        timeout=self.timeout
                    )
                    response.raise_for_status()
                    return response.json()
                except requests.exceptions.RequestException as e:
                    logger.error(f"HTTP request failed: {str(e)}")
                    if attempt < self.max_retries:
                        time.sleep(self.retry_delay)
                    else:
                        return {"error": f"HTTP request failed after {self.max_retries+1} attempts: {str(e)}"}
    
    def list_workflows(self) -> Dict[str, Any]:
        """List available workflows in the MCP server"""
        message = {
            "type": "message",
            "name": "listWorkflows",
            "data": {
                "operation": "listWorkflows",
                "workflowIds": "null",
                "parameters": "null"
            }
        }
        return self.send_message(message)
    
    def search_workflows(self) -> Dict[str, Any]:
        """Search for workflows in the MCP server"""
        message = {
            "type": "message",
            "name": "searchWorkflows",
            "data": {
                "operation": "searchWorkflows",
                "workflowIds": "null",
                "parameters": "null"
            }
        }
        return self.send_message(message)
    
    def add_workflow(self, workflow_ids: str) -> Dict[str, Any]:
        """Add workflow(s) to the available pool"""
        message = {
            "type": "message",
            "name": "addWorkflow",
            "data": {
                "operation": "addWorkflow",
                "workflowIds": workflow_ids,
                "parameters": "null"
            }
        }
        return self.send_message(message)
    
    def remove_workflow(self, workflow_ids: str) -> Dict[str, Any]:
        """Remove workflow(s) from the available pool"""
        message = {
            "type": "message",
            "name": "removeWorkflow",
            "data": {
                "operation": "removeWorkflow",
                "workflowIds": workflow_ids,
                "parameters": "null"
            }
        }
        return self.send_message(message)
    
    def execute_workflow(self, workflow_id: str, parameters: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a workflow with the given parameters"""
        message = {
            "type": "message",
            "name": "executeWorkflow",
            "data": {
                "operation": "executeWorkflow",
                "workflowIds": workflow_id,
                "parameters": json.dumps(parameters)
            }
        }
        return self.send_message(message)
    
    def custom_command(self, command_name: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Send a custom command to the MCP server"""
        message = {
            "type": "message",
            "name": command_name,
            "data": data
        }
        return self.send_message(message)

# Initialize client
@st.cache_resource
def get_client():
    return MCPClient(
        server_url=server_url,
        api_key=api_key,
        connection_type=connection_type,
        timeout=timeout,
        max_retries=max_retries,
        retry_delay=retry_delay
    )

client = get_client()

# Helper functions
def add_to_history(operation, request, response, success=True):
    """Add an operation to the history"""
    st.session_state.history.append({
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "operation": operation,
        "request": request,
        "response": response,
        "success": success
    })

def parse_parameters_schema(parameters_json):
    """Parse the parameters schema from JSON string"""
    try:
        if not parameters_json or parameters_json == "null":
            return {}
        
        schema = json.loads(parameters_json)
        if not isinstance(schema, dict):
            return {}
            
        return schema
    except json.JSONDecodeError:
        return {}

def render_parameter_inputs(schema, workflow_id):
    """Render input fields based on parameter schema"""
    if not schema or not isinstance(schema, dict):
        st.info("No parameters required for this workflow")
        return {}
    
    properties = schema.get("properties", {})
    if not properties:
        st.info("No parameters defined for this workflow")
        return {}
    
    # Initialize parameters in session state if not already present
    if workflow_id not in st.session_state.workflow_parameters:
        st.session_state.workflow_parameters[workflow_id] = {}
    
    parameters = {}
    
    for param_name, param_schema in properties.items():
        param_type = param_schema.get("type", "string")
        current_value = st.session_state.workflow_parameters[workflow_id].get(param_name, "")
        
        st.markdown(f"**{param_name}** ({param_type})")
        
        if param_type == "string":
            value = st.text_input(
                f"Enter value for {param_name}", 
                value=current_value,
                key=f"param_{workflow_id}_{param_name}"
            )
        elif param_type == "number" or param_type == "integer":
            value = st.number_input(
                f"Enter value for {param_name}", 
                value=float(current_value) if current_value else 0,
                key=f"param_{workflow_id}_{param_name}"
            )
        elif param_type == "boolean":
            value = st.checkbox(
                f"Enable {param_name}", 
                value=bool(current_value),
                key=f"param_{workflow_id}_{param_name}"
            )
        elif param_type == "object":
            value = st.text_area(
                f"Enter JSON for {param_name}", 
                value=current_value if current_value else "{}",
                key=f"param_{workflow_id}_{param_name}"
            )
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                st.error(f"Invalid JSON for {param_name}")
                value = {}
        elif param_type == "array":
            value = st.text_area(
                f"Enter JSON array for {param_name}", 
                value=current_value if current_value else "[]",
                key=f"param_{workflow_id}_{param_name}"
            )
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                st.error(f"Invalid JSON array for {param_name}")
                value = []
        else:
            value = st.text_input(
                f"Enter value for {param_name}", 
                value=current_value,
                key=f"param_{workflow_id}_{param_name}"
            )
        
        parameters[param_name] = value
        st.session_state.workflow_parameters[workflow_id][param_name] = value
    
    return parameters

# Main content
st.markdown("<div class='main-header'>n8n MCP Client</div>", unsafe_allow_html=True)

# Tabs for different functionalities
tab1, tab2, tab3, tab4, tab5 = st.tabs([
    "🔍 Discover Workflows", 
    "⚙️ Manage Workflows", 
    "▶️ Execute Workflows", 
    "📝 History", 
    "🧪 Custom Commands"
])

with tab1:
    st.markdown("<div class='sub-header'>Discover Available Workflows</div>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        if st.button("List Available Workflows", key="list_workflows"):
            with st.spinner("Fetching available workflows..."):
                response = client.list_workflows()
                
                if "error" in response:
                    st.error(f"Error: {response['error']}")
                    add_to_history("List Workflows", {}, response, False)
                else:
                    st.success("Workflows retrieved successfully!")
                    
                    if "response" in response:
                        workflows = response["response"]
                        st.session_state.workflows = workflows
                        add_to_history("List Workflows", {}, workflows, True)
                    else:
                        st.warning("No workflows found or unexpected response format")
                        add_to_history("List Workflows", {}, response, False)
    
    with col2:
        if st.button("Search All Workflows", key="search_workflows"):
            with st.spinner("Searching for workflows..."):
                response = client.search_workflows()
                
                if "error" in response:
                    st.error(f"Error: {response['error']}")
                    add_to_history("Search Workflows", {}, response, False)
                else:
                    st.success("Workflows found!")
                    
                    if "response" in response:
                        workflows = response["response"]
                        st.session_state.workflows = workflows
                        add_to_history("Search Workflows", {}, workflows, True)
                    else:
                        st.warning("No workflows found or unexpected response format")
                        add_to_history("Search Workflows", {}, response, False)
    
    # Display workflows
    if st.session_state.workflows:
        st.markdown("<div class='sub-header'>Available Workflows</div>", unsafe_allow_html=True)
        
        # Convert to DataFrame for better display
        workflow_data = []
        for wf in st.session_state.workflows:
            if isinstance(wf, dict):
                workflow_data.append({
                    "ID": wf.get("id", "Unknown"),
                    "Name": wf.get("name", "Unnamed Workflow"),
                    "Description": wf.get("description", "No description")
                })
        
        if workflow_data:
            df = pd.DataFrame(workflow_data)
            st.dataframe(df, use_container_width=True)
        else:
            st.info("No workflows available")
    else:
        st.info("No workflows loaded. Use the buttons above to discover workflows.")

with tab2:
    st.markdown("<div class='sub-header'>Manage Workflows</div>", unsafe_allow_html=True)
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        st.markdown("### Add Workflow")
        workflow_ids_to_add = st.text_input("Workflow ID(s) to add (comma-separated for multiple)")
        
        if st.button("Add Workflow(s)", key="add_workflow"):
            if not workflow_ids_to_add:
                st.error("Please enter at least one workflow ID")
            else:
                with st.spinner("Adding workflow(s)..."):
                    response = client.add_workflow(workflow_ids_to_add)
                    
                    if "error" in response:
                        st.error(f"Error: {response['error']}")
                        add_to_history("Add Workflow", {"workflowIds": workflow_ids_to_add}, response, False)
                    else:
                        st.success("Workflow(s) added successfully!")
                        add_to_history("Add Workflow", {"workflowIds": workflow_ids_to_add}, response, True)
    
    with col2:
        st.markdown("### Remove Workflow")
        workflow_ids_to_remove = st.text_input("Workflow ID(s) to remove (comma-separated for multiple)")
        
        if st.button("Remove Workflow(s)", key="remove_workflow"):
            if not workflow_ids_to_remove:
                st.error("Please enter at least one workflow ID")
            else:
                with st.spinner("Removing workflow(s)..."):
                    response = client.remove_workflow(workflow_ids_to_remove)
                    
                    if "error" in response:
                        st.error(f"Error: {response['error']}")
                        add_to_history("Remove Workflow", {"workflowIds": workflow_ids_to_remove}, response, False)
                    else:
                        st.success("Workflow(s) removed successfully!")
                        add_to_history("Remove Workflow", {"workflowIds": workflow_ids_to_remove}, response, True)

with tab3:
    st.markdown("<div class='sub-header'>Execute Workflows</div>", unsafe_allow_html=True)
    
    # Workflow selection
    workflow_options = []
    if st.session_state.workflows:
        for wf in st.session_state.workflows:
            if isinstance(wf, dict) and "id" in wf and "name" in wf:
                workflow_options.append({"id": wf["id"], "name": wf["name"]})
    
    if not workflow_options:
        st.warning("No workflows available. Go to the 'Discover Workflows' tab to find workflows.")
    else:
        # Create a dropdown with workflow names but store the IDs
        workflow_names = [f"{wf['name']} ({wf['id']})" for wf in workflow_options]
        selected_workflow_index = st.selectbox(
            "Select a workflow to execute",
            range(len(workflow_names)),
            format_func=lambda i: workflow_names[i]
        )
        
        selected_workflow = workflow_options[selected_workflow_index]
        st.session_state.selected_workflow = selected_workflow
        
        # Find the full workflow details
        workflow_details = None
        for wf in st.session_state.workflows:
            if isinstance(wf, dict) and wf.get("id") == selected_workflow["id"]:
                workflow_details = wf
                break
        
        if workflow_details:
            st.markdown(f"### {workflow_details.get('name', 'Unnamed Workflow')}")
            
            if "description" in workflow_details and workflow_details["description"]:
                st.markdown(f"**Description:** {workflow_details['description']}")
            
            st.markdown("### Parameters")
            
            # Parse and render parameter inputs
            parameters_schema = parse_parameters_schema(workflow_details.get("parameters", "{}"))
            parameters = render_parameter_inputs(parameters_schema, selected_workflow["id"])
            
            if st.button("Execute Workflow", key="execute_workflow"):
                with st.spinner(f"Executing workflow '{selected_workflow['name']}'..."):
                    response = client.execute_workflow(selected_workflow["id"], parameters)
                    
                    if "error" in response:
                        st.error(f"Error: {response['error']}")
                        add_to_history("Execute Workflow", {
                            "workflowId": selected_workflow["id"],
                            "parameters": parameters
                        }, response, False)
                    else:
                        st.success("Workflow executed successfully!")
                        
                        # Display the response
                        st.markdown("### Execution Result")
                        st.json(response)
                        
                        add_to_history("Execute Workflow", {
                            "workflowId": selected_workflow["id"],
                            "parameters": parameters
                        }, response, True)

with tab4:
    st.markdown("<div class='sub-header'>Operation History</div>", unsafe_allow_html=True)
    
    if not st.session_state.history:
        st.info("No operations have been performed yet.")
    else:
        # Add clear history button
        if st.button("Clear History"):
            st.session_state.history = []
            st.experimental_rerun()
        
        # Display history in reverse chronological order
        for i, entry in enumerate(reversed(st.session_state.history)):
            with st.expander(f"{entry['timestamp']} - {entry['operation']} {'✅' if entry['success'] else '❌'}"):
                col1, col2 = st.columns(2)
                
                with col1:
                    st.markdown("#### Request")
                    st.json(entry["request"])
                
                with col2:
                    st.markdown("#### Response")
                    st.json(entry["response"])

with tab5:
    st.markdown("<div class='sub-header'>Custom Commands</div>", unsafe_allow_html=True)
    
    command_name = st.text_input("Command Name")
    command_data = st.text_area("Command Data (JSON)", "{}", height=150)
    
    if st.button("Send Command"):
        if not command_name:
            st.warning("Please enter a command name")
        else:
            try:
                data = json.loads(command_data)
                
                with st.spinner("Sending command..."):
                    response = client.custom_command(command_name, data)
                    
                    if "error" in response:
                        st.error(f"Error: {response['error']}")
                        add_to_history("Custom Command", {
                            "command": command_name,
                            "data": data
                        }, response, False)
                    else:
                        st.success("Command executed successfully!")
                        st.subheader("Response")
                        st.json(response)
                        
                        add_to_history("Custom Command", {
                            "command": command_name,
                            "data": data
                        }, response, True)
            except json.JSONDecodeError:
                st.error("Invalid JSON data")

# Connection status indicator in sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("<div class='sub-header'>Connection Status</div>", unsafe_allow_html=True)

if connection_type == "WebSocket":
    if client.ws_connected:
        st.sidebar.success("WebSocket Connected")
        st.session_state.connection_status = "Connected"
    else:
        st.sidebar.warning("WebSocket Disconnected")
        st.session_state.connection_status = "Disconnected"
        if st.sidebar.button("Connect WebSocket"):
            client.connect_websocket()
            st.experimental_rerun()
else:
    # For HTTP, we'll test the connection
    if st.sidebar.button("Test Connection"):
        try:
            response = client.list_workflows()
            if "error" in response:
                st.sidebar.error(f"Connection Failed: {response['error']}")
                st.session_state.connection_status = "Error"
            else:
                st.sidebar.success("Connection Successful")
                st.session_state.connection_status = "Connected"
        except Exception as e:
            st.sidebar.error(f"Connection Failed: {str(e)}")
            st.session_state.connection_status = "Error"

# Display current status
status_color = {
    "Connected": "success",
    "Disconnected": "warning",
    "Error": "error"
}
st.sidebar.markdown(
    f"<div class='{status_color.get(st.session_state.connection_status, 'warning')}-msg'>"
    f"Status: {st.session_state.connection_status}</div>",
    unsafe_allow_html=True
)

# About section in sidebar
st.sidebar.markdown("---")
st.sidebar.markdown("### About")
st.sidebar.markdown("""
This is an MCP client for n8n workflows. Connect to your n8n MCP server to:
- Discover available workflows
- Add/remove workflows from the available pool
- Execute workflows with parameters
- View operation history
- Send custom commands
""")

# Footer
st.sidebar.markdown("---")
st.sidebar.markdown("© 2025 n8n MCP Client")
