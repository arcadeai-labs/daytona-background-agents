// webhook-test-server is a configurable test server for validating the CATE webhook hook system.
// It implements all webhook endpoints (health, access, pre, post) with configurable behavior.
//
// Usage:
//
//	go run ./tools/webhook-test-server -port 8888 -token secret123 -config config.yaml
//
// The server logs all incoming requests and provides configurable responses.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/fsnotify/fsnotify"
	"github.com/gin-gonic/gin"
	"gopkg.in/yaml.v3"

	"github.com/ArcadeAI/webhook-test-server/schema"
)

// =============================================================================
// Configuration Types
// =============================================================================

// HookConfig is the root configuration loaded from YAML.
type HookConfig struct {
	Health *HealthConfig `yaml:"health" json:"health"`
	Access *AccessConfig `yaml:"access" json:"access"`
	Pre    *PreConfig    `yaml:"pre" json:"pre"`
	Post   *PostConfig   `yaml:"post" json:"post"`
}

// HealthConfig controls health endpoint behavior.
type HealthConfig struct {
	Status string `yaml:"status" json:"status"` // healthy, degraded, unhealthy
}

// AccessConfig controls access hook behavior.
type AccessConfig struct {
	// DefaultAction: "allow" or "deny" - what to do if no rules match
	DefaultAction string `yaml:"default_action" json:"default_action"`

	// Rules are evaluated in order, first match wins
	Rules []AccessRule `yaml:"rules" json:"rules"`
}

// AccessRule defines a single access control rule.
type AccessRule struct {
	// Match conditions (all must match)
	UserID       string `yaml:"user_id" json:"user_id"` // Exact match or regex if starts with ~
	ToolkitMatch string `yaml:"toolkit" json:"toolkit"` // Toolkit name pattern
	ToolMatch    string `yaml:"tool" json:"tool"`       // Tool name pattern

	// Action: "allow" or "deny"
	Action string `yaml:"action" json:"action"`

	// Optional: reason for deny (returned in error)
	Reason string `yaml:"reason" json:"reason"`
}

// PreConfig controls pre-execution hook behavior.
type PreConfig struct {
	// DefaultAction: "proceed", "block", or "rate_limit"
	DefaultAction string `yaml:"default_action" json:"default_action"`

	// Rules are evaluated in order, first match wins
	Rules []PreRule `yaml:"rules" json:"rules"`
}

// PreRule defines a single pre-execution rule.
type PreRule struct {
	// Match conditions
	UserID      string `yaml:"user_id" json:"user_id"`
	Toolkit     string `yaml:"toolkit" json:"toolkit"`
	Tool        string `yaml:"tool" json:"tool"`
	ExecutionID string `yaml:"execution_id" json:"execution_id"`
	InputMatch  string `yaml:"input_match" json:"input_match"` // JSON path expression like "inputs.to contains @blocked.com"

	// Action: "proceed", "block", or "rate_limit"
	Action string `yaml:"action" json:"action"`

	// Error message when blocking
	ErrorMessage string `yaml:"error_message" json:"error_message"`

	// Overrides (only applied if action is "proceed")
	Override *PreOverrideConfig `yaml:"override" json:"override"`
}

// PreOverrideConfig defines what to override in pre-hook.
type PreOverrideConfig struct {
	Inputs  map[string]interface{} `yaml:"inputs" json:"inputs"`
	Secrets map[string]string      `yaml:"secrets" json:"secrets"`
	Headers map[string]string      `yaml:"headers" json:"headers"`
	Server  *ServerOverride        `yaml:"server" json:"server"`
}

// ServerOverride defines server routing override.
type ServerOverride struct {
	Name string `yaml:"name" json:"name"`
	URI  string `yaml:"uri" json:"uri"`
	Type string `yaml:"type" json:"type"` // arcade or mcp
}

// PostConfig controls post-execution hook behavior.
type PostConfig struct {
	// DefaultAction: "proceed", "block", or "rate_limit"
	DefaultAction string `yaml:"default_action" json:"default_action"`

	// Rules are evaluated in order, first match wins
	Rules []PostRule `yaml:"rules" json:"rules"`
}

// PostRule defines a single post-execution rule.
type PostRule struct {
	// Match conditions
	UserID      string `yaml:"user_id" json:"user_id"`
	Toolkit     string `yaml:"toolkit" json:"toolkit"`
	Tool        string `yaml:"tool" json:"tool"`
	ExecutionID string `yaml:"execution_id" json:"execution_id"`
	Success     *bool  `yaml:"success" json:"success"` // nil = any, true/false = match
	OutputMatch string `yaml:"output_match" json:"output_match"`

	// Action: "proceed", "block", or "rate_limit"
	Action string `yaml:"action" json:"action"`

	// Error message when blocking
	ErrorMessage string `yaml:"error_message" json:"error_message"`

	// Output override (only applied if action is "proceed")
	Override *PostOverrideConfig `yaml:"override" json:"override"`
}

// PostOverrideConfig defines what to override in post-hook.
type PostOverrideConfig struct {
	Output map[string]interface{} `yaml:"output" json:"output"`
}

// =============================================================================
// Request Logging
// =============================================================================

// RequestLog stores information about each incoming request.
type RequestLog struct {
	Timestamp time.Time   `json:"timestamp"`
	Endpoint  string      `json:"endpoint"`
	Body      interface{} `json:"body"`
	Headers   http.Header `json:"headers"`
	Response  interface{} `json:"response"`
	RuleMatch string      `json:"rule_match,omitempty"`
}

// =============================================================================
// Test Server
// =============================================================================

// ServerConfig holds the server configuration from CLI.
type ServerConfig struct {
	Port       int
	Token      string
	Verbose    bool
	ConfigFile string
}

// TestServer implements the webhook.ServerInterface for testing.
type TestServer struct {
	mu         sync.RWMutex
	logs       []RequestLog
	serverCfg  *ServerConfig
	hookConfig *HookConfig
}

// NewTestServer creates a new test server with default configuration.
func NewTestServer(serverCfg *ServerConfig) *TestServer {
	ts := &TestServer{
		logs:      make([]RequestLog, 0),
		serverCfg: serverCfg,
		hookConfig: &HookConfig{
			Health: &HealthConfig{Status: "healthy"},
			Access: &AccessConfig{DefaultAction: "allow"},
			Pre:    &PreConfig{DefaultAction: "proceed"},
			Post:   &PostConfig{DefaultAction: "proceed"},
		},
	}
	return ts
}

// LoadConfig loads configuration from a YAML file.
func (ts *TestServer) LoadConfig(path string) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return fmt.Errorf("failed to read config file: %w", err)
	}

	var cfg HookConfig
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return fmt.Errorf("failed to parse config file: %w", err)
	}

	ts.mu.Lock()
	defer ts.mu.Unlock()

	// Merge with defaults
	if cfg.Health != nil {
		ts.hookConfig.Health = cfg.Health
	}
	if cfg.Access != nil {
		ts.hookConfig.Access = cfg.Access
	}
	if cfg.Pre != nil {
		ts.hookConfig.Pre = cfg.Pre
	}
	if cfg.Post != nil {
		ts.hookConfig.Post = cfg.Post
	}

	return nil
}

// GetConfig returns current hook configuration.
func (ts *TestServer) GetConfig() *HookConfig {
	ts.mu.RLock()
	defer ts.mu.RUnlock()
	return ts.hookConfig
}

func (ts *TestServer) logRequest(endpoint string, body, response interface{}, ruleMatch string) {
	ts.mu.Lock()
	defer ts.mu.Unlock()

	entry := RequestLog{
		Timestamp: time.Now(),
		Endpoint:  endpoint,
		Body:      body,
		Response:  response,
		RuleMatch: ruleMatch,
	}
	ts.logs = append(ts.logs, entry)

	if ts.serverCfg.Verbose {
		jsonBody, _ := json.MarshalIndent(body, "", "  ")
		jsonResp, _ := json.MarshalIndent(response, "", "  ")
		fmt.Printf("\n📥 [%s] %s\n", time.Now().Format("15:04:05"), endpoint)
		if ruleMatch != "" {
			fmt.Printf("📋 Rule matched: %s\n", ruleMatch)
		}
		fmt.Printf("Request:\n%s\n", string(jsonBody))
		fmt.Printf("Response:\n%s\n", string(jsonResp))
		fmt.Println(strings.Repeat("-", 60))
	}
}

// GetLogs returns all logged requests.
func (ts *TestServer) GetLogs() []RequestLog {
	ts.mu.RLock()
	defer ts.mu.RUnlock()
	return append([]RequestLog{}, ts.logs...)
}

// ClearLogs clears all logged requests.
func (ts *TestServer) ClearLogs() {
	ts.mu.Lock()
	defer ts.mu.Unlock()
	ts.logs = make([]RequestLog, 0)
}

// =============================================================================
// Webhook Handlers
// =============================================================================

// HealthCheck implements webhook.ServerInterface.
func (ts *TestServer) HealthCheck(c *gin.Context) {
	cfg := ts.GetConfig()
	status := schema.HealthResponseStatus(cfg.Health.Status)
	resp := schema.HealthResponse{Status: &status}

	ts.logRequest("/health", nil, resp, "")
	c.JSON(http.StatusOK, resp)
}

// AccessHook implements webhook.ServerInterface.
func (ts *TestServer) AccessHook(c *gin.Context) {
	if !ts.validateAuth(c) {
		return
	}

	var req schema.AccessHookRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, schema.ErrorResponse{
			Error: strPtr("invalid request body: " + err.Error()),
			Code:  strPtr("INVALID_REQUEST"),
		})
		return
	}

	resp, ruleMatch := ts.evaluateAccessRules(req)
	ts.logRequest("/access", req, resp, ruleMatch)
	c.JSON(http.StatusOK, resp)
}

func (ts *TestServer) evaluateAccessRules(req schema.AccessHookRequest) (*schema.AccessHookResult, string) {
	cfg := ts.GetConfig()
	accessCfg := cfg.Access

	// Build allow/deny lists based on rules
	allow := make(schema.Toolkits)
	deny := make(schema.Toolkits)
	ruleMatch := ""

	for toolkitName, toolkitInfo := range req.Toolkits {
		if toolkitInfo.Tools == nil {
			continue
		}
		for toolName, versions := range *toolkitInfo.Tools {
			action, matchedRule := ts.matchAccessRule(accessCfg, req.UserId, toolkitName, toolName)
			if matchedRule != "" {
				ruleMatch = matchedRule
			}

			if action == "deny" {
				if _, ok := deny[toolkitName]; !ok {
					deny[toolkitName] = schema.ToolkitInfo{Tools: &map[string][]schema.ToolVersionInfo{}}
				}
				(*deny[toolkitName].Tools)[toolName] = versions
			} else {
				if _, ok := allow[toolkitName]; !ok {
					allow[toolkitName] = schema.ToolkitInfo{Tools: &map[string][]schema.ToolVersionInfo{}}
				}
				(*allow[toolkitName].Tools)[toolName] = versions
			}
		}
	}

	result := &schema.AccessHookResult{}
	if len(allow) > 0 {
		result.Allow = &allow
	}
	if len(deny) > 0 {
		result.Deny = &deny
	}

	return result, ruleMatch
}

func (ts *TestServer) matchAccessRule(cfg *AccessConfig, userID, toolkit, tool string) (string, string) {
	for i, rule := range cfg.Rules {
		if ts.matchesPattern(rule.UserID, userID) &&
			ts.matchesPattern(rule.ToolkitMatch, toolkit) &&
			ts.matchesPattern(rule.ToolMatch, tool) {
			return rule.Action, fmt.Sprintf("access.rules[%d]", i)
		}
	}
	return cfg.DefaultAction, ""
}

// PreHook implements webhook.ServerInterface.
func (ts *TestServer) PreHook(c *gin.Context) {
	if !ts.validateAuth(c) {
		return
	}

	var req schema.PreHookRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, schema.ErrorResponse{
			Error: strPtr("invalid request body: " + err.Error()),
			Code:  strPtr("INVALID_REQUEST"),
		})
		return
	}

	resp, ruleMatch := ts.evaluatePreRules(req)
	ts.logRequest("/pre", req, resp, ruleMatch)
	c.JSON(http.StatusOK, resp)
}

func (ts *TestServer) evaluatePreRules(req schema.PreHookRequest) (*schema.PreHookResult, string) {
	cfg := ts.GetConfig()
	preCfg := cfg.Pre

	userID := ""
	if req.Context.UserId != nil {
		userID = *req.Context.UserId
	}

	for i, rule := range preCfg.Rules {
		if ts.matchPreRule(rule, userID, req) {
			result := ts.applyPreRule(rule)
			return result, fmt.Sprintf("pre.rules[%d]", i)
		}
	}

	// Apply default action
	return &schema.PreHookResult{
		Code: ts.actionToCode(preCfg.DefaultAction),
	}, ""
}

func (ts *TestServer) matchPreRule(rule PreRule, userID string, req schema.PreHookRequest) bool {
	if !ts.matchesPattern(rule.UserID, userID) {
		return false
	}
	if !ts.matchesPattern(rule.Toolkit, req.Tool.Toolkit) {
		return false
	}
	if !ts.matchesPattern(rule.Tool, req.Tool.Name) {
		return false
	}
	if !ts.matchesPattern(rule.ExecutionID, req.ExecutionId) {
		return false
	}
	if rule.InputMatch != "" && !ts.matchesInputs(rule.InputMatch, req.Inputs) {
		return false
	}
	return true
}

func (ts *TestServer) applyPreRule(rule PreRule) *schema.PreHookResult {
	result := &schema.PreHookResult{
		Code: ts.actionToCode(rule.Action),
	}

	if rule.ErrorMessage != "" {
		result.ErrorMessage = &rule.ErrorMessage
	}

	if rule.Override != nil && rule.Action == "proceed" {
		override := &schema.PreHookOverride{}

		if len(rule.Override.Inputs) > 0 {
			override.Inputs = &rule.Override.Inputs
		}
		if len(rule.Override.Headers) > 0 {
			override.Headers = &rule.Override.Headers
		}
		if len(rule.Override.Secrets) > 0 {
			secrets := []map[string]string{rule.Override.Secrets}
			override.Secrets = &secrets
		}
		if rule.Override.Server != nil {
			override.Server = &schema.ServerInfo{
				Name: rule.Override.Server.Name,
				Uri:  rule.Override.Server.URI,
				Type: schema.ServerInfoType(rule.Override.Server.Type),
			}
		}

		result.Override = override
	}

	return result
}

// PostHook implements webhook.ServerInterface.
func (ts *TestServer) PostHook(c *gin.Context) {
	if !ts.validateAuth(c) {
		return
	}

	var req schema.PostHookRequest
	if err := c.ShouldBindJSON(&req); err != nil {
		c.JSON(http.StatusBadRequest, schema.ErrorResponse{
			Error: strPtr("invalid request body: " + err.Error()),
			Code:  strPtr("INVALID_REQUEST"),
		})
		return
	}

	resp, ruleMatch := ts.evaluatePostRules(req)
	ts.logRequest("/post", req, resp, ruleMatch)
	c.JSON(http.StatusOK, resp)
}

func (ts *TestServer) evaluatePostRules(req schema.PostHookRequest) (*schema.PostHookResult, string) {
	cfg := ts.GetConfig()
	postCfg := cfg.Post

	userID := ""
	if req.Context.UserId != nil {
		userID = *req.Context.UserId
	}

	for i, rule := range postCfg.Rules {
		if ts.matchPostRule(rule, userID, req) {
			result := ts.applyPostRule(rule)
			return result, fmt.Sprintf("post.rules[%d]", i)
		}
	}

	return &schema.PostHookResult{
		Code: ts.actionToCode(postCfg.DefaultAction),
	}, ""
}

func (ts *TestServer) matchPostRule(rule PostRule, userID string, req schema.PostHookRequest) bool {
	if !ts.matchesPattern(rule.UserID, userID) {
		return false
	}
	if !ts.matchesPattern(rule.Toolkit, req.Tool.Toolkit) {
		return false
	}
	if !ts.matchesPattern(rule.Tool, req.Tool.Name) {
		return false
	}
	if !ts.matchesPattern(rule.ExecutionID, req.ExecutionId) {
		return false
	}
	if rule.Success != nil && req.Success != nil && *rule.Success != *req.Success {
		return false
	}
	if rule.OutputMatch != "" && !ts.matchesOutput(rule.OutputMatch, req.Output) {
		return false
	}
	return true
}

func (ts *TestServer) applyPostRule(rule PostRule) *schema.PostHookResult {
	result := &schema.PostHookResult{
		Code: ts.actionToCode(rule.Action),
	}

	if rule.ErrorMessage != "" {
		result.ErrorMessage = &rule.ErrorMessage
	}

	if rule.Override != nil && rule.Action == "proceed" {
		if len(rule.Override.Output) > 0 {
			result.Override = &schema.PostHookOverride{
				Output: &rule.Override.Output,
			}
		}
	}

	return result
}

// =============================================================================
// Helper Functions
// =============================================================================

func (ts *TestServer) validateAuth(c *gin.Context) bool {
	if ts.serverCfg.Token == "" {
		return true
	}

	auth := c.GetHeader("Authorization")
	expected := "Bearer " + ts.serverCfg.Token
	if auth != expected {
		c.JSON(http.StatusUnauthorized, schema.ErrorResponse{
			Error: strPtr("invalid or missing bearer token"),
			Code:  strPtr("UNAUTHORIZED"),
		})
		return false
	}
	return true
}

func (ts *TestServer) matchesPattern(pattern, value string) bool {
	if pattern == "" {
		return true // Empty pattern matches everything
	}
	if strings.HasPrefix(pattern, "~") {
		// Regex pattern
		re, err := regexp.Compile(pattern[1:])
		if err != nil {
			return false
		}
		return re.MatchString(value)
	}
	if strings.Contains(pattern, "*") {
		// Glob pattern - convert to regex
		regexPattern := "^" + strings.ReplaceAll(regexp.QuoteMeta(pattern), "\\*", ".*") + "$"
		re, err := regexp.Compile(regexPattern)
		if err != nil {
			return false
		}
		return re.MatchString(value)
	}
	return pattern == value
}

func (ts *TestServer) matchesInputs(expr string, inputs map[string]interface{}) bool {
	// Simple matching: "key=value" or "key contains value"
	if strings.Contains(expr, " contains ") {
		parts := strings.SplitN(expr, " contains ", 2)
		if len(parts) != 2 {
			return false
		}
		key, substring := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if val, ok := inputs[key]; ok {
			return strings.Contains(fmt.Sprintf("%v", val), substring)
		}
		return false
	}
	if strings.Contains(expr, "=") {
		parts := strings.SplitN(expr, "=", 2)
		if len(parts) != 2 {
			return false
		}
		key, expected := strings.TrimSpace(parts[0]), strings.TrimSpace(parts[1])
		if val, ok := inputs[key]; ok {
			return fmt.Sprintf("%v", val) == expected
		}
		return false
	}
	// Just check if key exists
	_, ok := inputs[expr]
	return ok
}

func (ts *TestServer) matchesOutput(expr string, output map[string]interface{}) bool {
	return ts.matchesInputs(expr, output)
}

func (ts *TestServer) actionToCode(action string) schema.ResponseCode {
	switch action {
	case "proceed", "allow", "":
		return schema.OK
	case "block", "deny":
		return schema.CHECKFAILED
	case "rate_limit":
		return schema.RATELIMITEXCEEDED
	default:
		return schema.OK
	}
}

func strPtr(s string) *string {
	return &s
}

// =============================================================================
// Debug/Admin Endpoints
// =============================================================================

func (ts *TestServer) handleGetLogs(c *gin.Context) {
	logs := ts.GetLogs()
	c.JSON(http.StatusOK, gin.H{
		"count": len(logs),
		"logs":  logs,
	})
}

func (ts *TestServer) handleClearLogs(c *gin.Context) {
	ts.ClearLogs()
	c.JSON(http.StatusOK, gin.H{
		"message": "logs cleared",
	})
}

func (ts *TestServer) handleStatus(c *gin.Context) {
	c.JSON(http.StatusOK, gin.H{
		"status":        "running",
		"port":          ts.serverCfg.Port,
		"auth_enabled":  ts.serverCfg.Token != "",
		"config_file":   ts.serverCfg.ConfigFile,
		"request_count": len(ts.GetLogs()),
	})
}

func (ts *TestServer) handleGetConfig(c *gin.Context) {
	c.JSON(http.StatusOK, ts.GetConfig())
}

func (ts *TestServer) handleSetConfig(c *gin.Context) {
	var cfg HookConfig
	if err := c.ShouldBindJSON(&cfg); err != nil {
		c.JSON(http.StatusBadRequest, gin.H{"error": err.Error()})
		return
	}

	ts.mu.Lock()
	if cfg.Health != nil {
		ts.hookConfig.Health = cfg.Health
	}
	if cfg.Access != nil {
		ts.hookConfig.Access = cfg.Access
	}
	if cfg.Pre != nil {
		ts.hookConfig.Pre = cfg.Pre
	}
	if cfg.Post != nil {
		ts.hookConfig.Post = cfg.Post
	}
	ts.mu.Unlock()

	c.JSON(http.StatusOK, gin.H{"message": "configuration updated"})
}

// =============================================================================
// Main
// =============================================================================

func main() {
	serverCfg := &ServerConfig{}

	flag.IntVar(&serverCfg.Port, "port", 8888, "Port to listen on")
	flag.StringVar(&serverCfg.Token, "token", "", "Bearer token for authentication (empty = no auth)")
	flag.BoolVar(&serverCfg.Verbose, "verbose", true, "Log all requests to stdout")
	flag.StringVar(&serverCfg.ConfigFile, "config", "", "Path to YAML configuration file")
	flag.Parse()

	ts := NewTestServer(serverCfg)

	// Load configuration if specified
	if serverCfg.ConfigFile != "" {
		if err := ts.LoadConfig(serverCfg.ConfigFile); err != nil {
			log.Printf("⚠️  Warning: Failed to load config: %v", err)
		} else {
			log.Printf("✅ Loaded configuration from %s", serverCfg.ConfigFile)

			// Watch for config file changes
			go watchConfigFile(serverCfg.ConfigFile, ts)
		}
	}

	gin.SetMode(gin.ReleaseMode)
	router := gin.New()
	router.Use(gin.Recovery())

	// Register webhook handlers from generated code
	schema.RegisterHandlers(router, ts)

	// Debug/admin endpoints
	router.GET("/_logs", ts.handleGetLogs)
	router.DELETE("/_logs", ts.handleClearLogs)
	router.GET("/_status", ts.handleStatus)
	router.GET("/_config", ts.handleGetConfig)
	router.PUT("/_config", ts.handleSetConfig)
	router.POST("/_config", ts.handleSetConfig)

	printBanner(serverCfg)

	addr := fmt.Sprintf(":%d", serverCfg.Port)
	if err := router.Run(addr); err != nil {
		log.Fatal("Failed to start server:", err)
	}
}

func watchConfigFile(path string, ts *TestServer) {
	watcher, err := fsnotify.NewWatcher()
	if err != nil {
		log.Printf("Failed to create file watcher: %v", err)
		return
	}
	defer watcher.Close()

	if err := watcher.Add(path); err != nil {
		log.Printf("Failed to watch config file: %v", err)
		return
	}

	sigChan := make(chan os.Signal, 1)
	signal.Notify(sigChan, syscall.SIGINT, syscall.SIGTERM)

	for {
		select {
		case event, ok := <-watcher.Events:
			if !ok {
				return
			}
			handleConfigChange(event, path, ts)
		case err, ok := <-watcher.Errors:
			if !ok {
				return
			}
			log.Printf("Watcher error: %v", err)
		case <-sigChan:
			return
		}
	}
}

func handleConfigChange(event fsnotify.Event, path string, ts *TestServer) {
	if event.Op&fsnotify.Write != fsnotify.Write {
		return
	}

	log.Printf("🔄 Config file changed, reloading...")
	if err := ts.LoadConfig(path); err != nil {
		log.Printf("⚠️  Failed to reload config: %v", err)
		return
	}
	log.Printf("✅ Configuration reloaded")
}

func printBanner(cfg *ServerConfig) {
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("🚀 CATE Webhook Test Server")
	fmt.Println(strings.Repeat("=", 60))
	fmt.Printf("  Port:        %d\n", cfg.Port)
	fmt.Printf("  Auth:        %s\n", authStatus(cfg.Token))
	fmt.Printf("  Config:      %s\n", configStatus(cfg.ConfigFile))
	fmt.Println(strings.Repeat("-", 60))
	fmt.Println("Webhook Endpoints:")
	fmt.Printf("  GET  http://localhost:%d/health  - Health check\n", cfg.Port)
	fmt.Printf("  POST http://localhost:%d/access  - Access control hook\n", cfg.Port)
	fmt.Printf("  POST http://localhost:%d/pre     - Pre-execution hook\n", cfg.Port)
	fmt.Printf("  POST http://localhost:%d/post    - Post-execution hook\n", cfg.Port)
	fmt.Println()
	fmt.Println("Admin Endpoints:")
	fmt.Printf("  GET    http://localhost:%d/_status  - Server status\n", cfg.Port)
	fmt.Printf("  GET    http://localhost:%d/_logs    - View request logs\n", cfg.Port)
	fmt.Printf("  DELETE http://localhost:%d/_logs    - Clear request logs\n", cfg.Port)
	fmt.Printf("  GET    http://localhost:%d/_config  - View current config\n", cfg.Port)
	fmt.Printf("  PUT    http://localhost:%d/_config  - Update config (JSON)\n", cfg.Port)
	fmt.Println(strings.Repeat("=", 60))
	fmt.Println("Ready to receive webhook requests...")
	fmt.Println()
}

func authStatus(token string) string {
	if token == "" {
		return "disabled"
	}
	return fmt.Sprintf("enabled (token: %s...)", token[:min(8, len(token))])
}

func configStatus(path string) string {
	if path == "" {
		return "none (using defaults)"
	}
	return path + " (hot-reload enabled)"
}
