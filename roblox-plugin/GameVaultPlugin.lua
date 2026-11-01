--!strict
-- GameVault Studio bridge.
-- Install this file as a local Roblox Studio plugin while developing.
-- It talks only to GameVault on localhost and contains no API key.

local HttpService = game:GetService("HttpService")

local DEFAULT_PORT = "37421"
local savedPort = plugin:GetSetting("GameVaultPort")
local port = typeof(savedPort) == "string" and savedPort or DEFAULT_PORT

local function baseUrl(): string
	return "http://127.0.0.1:" .. port
end

local toolbar = plugin:CreateToolbar("GameVault")
local toolbarButton = toolbar:CreateButton("GameVault", "Open the GameVault bridge", "", "GameVault")
toolbarButton.ClickableWhenViewportHidden = true

local widgetInfo = DockWidgetPluginGuiInfo.new(
	Enum.InitialDockState.Float,
	false,
	false,
	390,
	560,
	300,
	360
)
local widget = plugin:CreateDockWidgetPluginGuiAsync("GameVaultStudioBridge", widgetInfo)
widget.Title = "GameVault"

local root = Instance.new("Frame")
root.BackgroundColor3 = Color3.fromRGB(11, 20, 34)
root.BorderSizePixel = 0
root.Size = UDim2.fromScale(1, 1)
root.Parent = widget

local padding = Instance.new("UIPadding")
padding.PaddingTop = UDim.new(0, 16)
padding.PaddingBottom = UDim.new(0, 16)
padding.PaddingLeft = UDim.new(0, 16)
padding.PaddingRight = UDim.new(0, 16)
padding.Parent = root

local layout = Instance.new("UIListLayout")
layout.Padding = UDim.new(0, 10)
layout.SortOrder = Enum.SortOrder.LayoutOrder
layout.Parent = root

local function label(text: string, height: number?, color: Color3?): TextLabel
	local item = Instance.new("TextLabel")
	item.BackgroundTransparency = 1
	item.Font = Enum.Font.Gotham
	item.Text = text
	item.TextColor3 = color or Color3.fromRGB(215, 229, 246)
	item.TextSize = 13
	item.TextXAlignment = Enum.TextXAlignment.Left
	item.TextWrapped = true
	item.Size = UDim2.new(1, 0, 0, height or 26)
	item.Parent = root
	return item
end

local title = label("◈  GameVault", 32, Color3.fromRGB(24, 182, 255))
title.Font = Enum.Font.GothamBold
title.TextSize = 20

local status = label("Connexion en attente…", 32, Color3.fromRGB(119, 144, 170))

local portRow = Instance.new("Frame")
portRow.BackgroundTransparency = 1
portRow.Size = UDim2.new(1, 0, 0, 32)
portRow.Parent = root

local portBox = Instance.new("TextBox")
portBox.BackgroundColor3 = Color3.fromRGB(7, 15, 26)
portBox.BorderColor3 = Color3.fromRGB(29, 48, 72)
portBox.ClearTextOnFocus = false
portBox.Font = Enum.Font.Gotham
portBox.PlaceholderText = DEFAULT_PORT
portBox.Text = port
portBox.TextColor3 = Color3.fromRGB(215, 229, 246)
portBox.TextSize = 12
portBox.Size = UDim2.new(0.52, -5, 1, 0)
portBox.Parent = portRow

local savePort = Instance.new("TextButton")
savePort.BackgroundColor3 = Color3.fromRGB(20, 94, 150)
savePort.BorderSizePixel = 0
savePort.Font = Enum.Font.GothamBold
savePort.Text = "Enregistrer le port"
savePort.TextColor3 = Color3.fromRGB(255, 255, 255)
savePort.TextSize = 11
savePort.Size = UDim2.new(0.48, -5, 1, 0)
savePort.Position = UDim2.new(0.52, 10, 0, 0)
savePort.Parent = portRow

local refreshButton = Instance.new("TextButton")
refreshButton.BackgroundColor3 = Color3.fromRGB(17, 125, 98)
refreshButton.BorderSizePixel = 0
refreshButton.Font = Enum.Font.GothamBold
refreshButton.Text = "Actualiser les projets Roblox"
refreshButton.TextColor3 = Color3.fromRGB(255, 255, 255)
refreshButton.TextSize = 12
refreshButton.Size = UDim2.new(1, 0, 0, 34)
refreshButton.Parent = root

local openButton = Instance.new("TextButton")
openButton.BackgroundColor3 = Color3.fromRGB(20, 94, 150)
openButton.BorderSizePixel = 0
openButton.Font = Enum.Font.GothamBold
openButton.Text = "Ouvrir GameVault"
openButton.TextColor3 = Color3.fromRGB(255, 255, 255)
openButton.TextSize = 12
openButton.Size = UDim2.new(1, 0, 0, 34)
openButton.Parent = root

local gamesTitle = label("Projets Roblox disponibles", 24, Color3.fromRGB(215, 229, 246))
gamesTitle.Font = Enum.Font.GothamBold

local gamesFrame = Instance.new("ScrollingFrame")
gamesFrame.BackgroundTransparency = 1
gamesFrame.BorderSizePixel = 0
gamesFrame.CanvasSize = UDim2.new()
gamesFrame.AutomaticCanvasSize = Enum.AutomaticSize.Y
gamesFrame.ScrollBarThickness = 5
gamesFrame.Size = UDim2.new(1, 0, 1, -240)
gamesFrame.Parent = root

local gamesLayout = Instance.new("UIListLayout")
gamesLayout.Padding = UDim.new(0, 6)
gamesLayout.SortOrder = Enum.SortOrder.LayoutOrder
gamesLayout.Parent = gamesFrame

local function setStatus(text: string, color: Color3?)
	status.Text = text
	status.TextColor3 = color or Color3.fromRGB(119, 144, 170)
end

local function request(path: string): any
	local response = HttpService:RequestAsync({
		Url = baseUrl() .. path,
		Method = "GET",
		Headers = { ["Accept"] = "application/json" },
	})
	if not response.Success then
		error("GameVault HTTP " .. tostring(response.StatusCode))
	end
	return HttpService:JSONDecode(response.Body)
end

local function clearGames()
	for _, child in ipairs(gamesFrame:GetChildren()) do
		if child:IsA("TextLabel") then
			child:Destroy()
		end
	end
end

local function refresh()
	setStatus("Connexion à " .. baseUrl() .. "…")
	clearGames()
	local ok, health = pcall(request, "/api/roblox/health")
	if not ok or not health.success then
		setStatus("GameVault introuvable. Lance l’application desktop.", Color3.fromRGB(255, 125, 145))
		return
	end
	local loaded, data = pcall(request, "/api/roblox/catalogue")
	if not loaded or not data.success then
		setStatus("Pont actif, catalogue indisponible.", Color3.fromRGB(255, 205, 100))
		return
	end
	local games = data.games or {}
	setStatus("Pont GameVault connecté · " .. tostring(#games) .. " projet(s)", Color3.fromRGB(50, 230, 170))
	if #games == 0 then
		local empty = label("Aucun fichier .rbxl/.rbxlx publié dans GameVault.", 30, Color3.fromRGB(119, 144, 170))
		empty.Parent = gamesFrame
		return
	end
	for _, game in ipairs(games) do
		local item = label((game.emoji or "🎮") .. "  " .. (game.titre or game.fichier), 34, Color3.fromRGB(215, 229, 246))
		item.BackgroundColor3 = Color3.fromRGB(7, 15, 26)
		item.BackgroundTransparency = 0
		item.TextTruncate = Enum.TextTruncate.AtEnd
		item.Parent = gamesFrame
	end
end

savePort.MouseButton1Click:Connect(function()
	local newPort = portBox.Text:match("^%d+$")
	if not newPort or tonumber(newPort) < 1 or tonumber(newPort) > 65535 then
		setStatus("Port invalide.", Color3.fromRGB(255, 125, 145))
		return
	end
	port = newPort
	plugin:SetSetting("GameVaultPort", port)
	setStatus("Port enregistré : " .. port, Color3.fromRGB(50, 230, 170))
end)

refreshButton.MouseButton1Click:Connect(refresh)
openButton.MouseButton1Click:Connect(function()
	local ok = pcall(function()
		plugin:OpenWikiPage(baseUrl() .. "/store.html")
	end)
	if not ok then
		setStatus("Impossible d’ouvrir GameVault.", Color3.fromRGB(255, 125, 145))
	end
end)

toolbarButton.Click:Connect(function()
	widget.Enabled = not widget.Enabled
	if widget.Enabled then
		refresh()
	end
end)

widget:BindToClose(function()
	widget.Enabled = false
end)
