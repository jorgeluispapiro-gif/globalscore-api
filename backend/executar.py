from app import criar_aplicacao


aplicacao = criar_aplicacao()


if __name__ == "__main__":
    aplicacao.run(host="0.0.0.0", port=5000, debug=True)

