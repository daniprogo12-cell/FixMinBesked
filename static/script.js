const toneButtons = document.querySelectorAll(".tone-btn");
const rewriteBtn = document.getElementById("rewriteBtn");
const copyBtn = document.getElementById("copyBtn");
const messageInput = document.getElementById("message");
const outputBox = document.getElementById("output");
const statusMessage = document.getElementById("statusMessage");
const loader = document.getElementById("loader");

let selectedTone = "";

// Sæt første aktive knap ved load
const defaultActiveButton = document.querySelector(".tone-btn.active") || toneButtons[0];

if (defaultActiveButton) {
    selectedTone = defaultActiveButton.dataset.tone;
    defaultActiveButton.classList.add("active");
    setStatus(`Valgt stil: ${selectedTone}`, "success");
}

// Klik på tone-knapper
toneButtons.forEach((button) => {
    button.addEventListener("click", () => {
        toneButtons.forEach((btn) => btn.classList.remove("active"));
        button.classList.add("active");

        selectedTone = button.dataset.tone;
        setStatus(`Valgt stil: ${selectedTone}`, "success");
    });
});

// Rewrite-knap
rewriteBtn.addEventListener("click", async () => {
    const text = messageInput.value.trim();

    if (!text) {
        setStatus("Du skal indsætte en besked.", "error");
        messageInput.focus();
        return;
    }

    if (!selectedTone) {
        setStatus("Du skal vælge en stil.", "error");
        return;
    }

    setLoadingState(true);
    loader.classList.remove("hidden");
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
        setStatus(error.message || "Noget gik galt.", "error");
    } finally {
        setLoadingState(false);
        loader.classList.add("hidden");
    }
});

// Kopiér-knap
copyBtn.addEventListener("click", async () => {
    const text = outputBox.textContent.trim();

    if (!text || text === "Her vises den forbedrede tekst...") {
        setStatus("Der er ikke noget at kopiere endnu.", "error");
        return;
    }

    try {
        await navigator.clipboard.writeText(text);
        setStatus("Teksten er kopieret.", "success");
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

function setStatus(message, type = "") {
    statusMessage.textContent = message;
    statusMessage.className = "status-message";

    if (type) {
        statusMessage.classList.add(type);
    }
}