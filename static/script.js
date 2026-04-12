const toneButtons = document.querySelectorAll(".tone-btn");
const rewriteBtn = document.getElementById("rewriteBtn");
const copyBtn = document.getElementById("copyBtn");
const messageInput = document.getElementById("message");
const outputBox = document.getElementById("output");
const statusMessage = document.getElementById("statusMessage");
const loader = document.getElementById("loader");
const MAX_CHARS = 2000;
const charCounter = document.getElementById("charCounter");

let selectedTone = "";

const defaultActiveButton = document.querySelector(".tone-btn.active") || toneButtons[0];

if (defaultActiveButton) {
    selectedTone = defaultActiveButton.dataset.tone;
    defaultActiveButton.classList.add("active");
    setStatus(`Valgt stil: ${selectedTone}`, "success");
}

messageInput.addEventListener("input", () => {
    const remaining = MAX_CHARS - messageInput.value.length;
    charCounter.textContent = `${remaining} tegn tilbage`;
    charCounter.style.color = remaining < 100 ? "red" : "";
});

async function trackEvent(eventType, buttonName) {
    try {
        await fetch("/track-event", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                event_type: eventType,
                button: buttonName,
            }),
        });
    } catch (error) {
        console.error("Kunne ikke tracke event:", error);
    }
}

toneButtons.forEach((button) => {
    button.addEventListener("click", () => {
        toneButtons.forEach((btn) => btn.classList.remove("active"));
        button.classList.add("active");

        selectedTone = button.dataset.tone;
        setStatus(`Valgt stil: ${selectedTone}`, "success");
    });
});

rewriteBtn.addEventListener("click", async () => {
    const text = messageInput.value.trim();

    if (!text) {
        setStatus("Du skal indsætte en besked.", "error");
        messageInput.focus();
        return;
    }

    if (text.length > MAX_CHARS) {
        setStatus("Beskeden er for lang. Hold dig under 2000 tegn.", "error");
        return;
    }

    if (!selectedTone) {
        setStatus("Du skal vælge en stil.", "error");
        return;
    }

    setLoadingState(true);
    setToneButtonsDisabled(true);
    loader.classList.remove("hidden");
    outputBox.textContent = "Arbejder på din besked...";
    setStatus("Forbedrer din besked...", "");

    try {
        const response = await fetch("/rewrite", {
            method: "POST",
            headers: {
                "Content-Type": "application/json",
            },
            body: JSON.stringify({
                text,
                tone: selectedTone,
            }),
        });

        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || "Noget gik galt.");
        }

        outputBox.textContent = data.result;
        setStatus(`Beskeden er klar i stilen: ${selectedTone}`, "success");
    } catch (error) {
        outputBox.textContent = "Her vises den forbedrede tekst...";
        setStatus(error.message || "Noget gik galt.", "error");
    } finally {
        setLoadingState(false);
        setToneButtonsDisabled(false);
        loader.classList.add("hidden");
    }
});

copyBtn.addEventListener("click", async () => {
    const text = outputBox.textContent.trim();

    if (
        !text ||
        text === "Her vises den forbedrede tekst..." ||
        text === "Arbejder på din besked..."
    ) {
        setStatus("Der er ikke noget at kopiere endnu.", "error");
        return;
    }

    try {
        await navigator.clipboard.writeText(text);
        setStatus("Teksten er kopieret.", "success");

        if (selectedTone) {
            await trackEvent("copy", selectedTone);
        }
    } catch (error) {
        setStatus("Kunne ikke kopiere teksten.", "error");
    }
});

function setLoadingState(isLoading) {
    rewriteBtn.disabled = isLoading;

    if (isLoading) {
        rewriteBtn.textContent = "Arbejder...";
        rewriteBtn.style.opacity = "0.85";
        rewriteBtn.style.cursor = "wait";
    } else {
        rewriteBtn.textContent = "Fix min besked";
        rewriteBtn.style.opacity = "1";
        rewriteBtn.style.cursor = "pointer";
    }
}

function setToneButtonsDisabled(isDisabled) {
    toneButtons.forEach((button) => {
        button.disabled = isDisabled;
        button.style.opacity = isDisabled ? "0.7" : "1";
        button.style.cursor = isDisabled ? "not-allowed" : "pointer";
    });
}

function setStatus(message, type = "") {
    statusMessage.textContent = message;
    statusMessage.className = "status-message";

    if (type) {
        statusMessage.classList.add(type);
    }
}