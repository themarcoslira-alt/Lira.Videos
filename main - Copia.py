"""
main.py — CLI do ULTRACUT3
Apenas CLI, nunca importado pela GUI
"""
import sys
import json
import argparse
from services.pipeline_service import PipelineService


def main():
    parser = argparse.ArgumentParser(description="ULTRACUT3 - Pipeline de vídeo")
    parser.add_argument("--projeto", "-p", help="Nome do projeto")
    parser.add_argument("--video", "-v", help="Arquivo de vídeo de entrada")
    parser.add_argument("--etapa", "-e", choices=["transcrever", "cenas", "storyboard",
                                                   "queries", "buscar", "renderizar",
                                                   "completo"],
                       default="completo", help="Etapa do pipeline")
    parser.add_argument("--listar", action="store_true", help="Listar projetos")
    parser.add_argument("--criar", metavar="NOME", help="Criar novo projeto")

    args = parser.parse_args()

    service = PipelineService()

    if args.listar:
        projetos = service.listar_projetos()
        print(json.dumps(projetos, indent=2, ensure_ascii=False))
        return

    if args.criar:
        result = service.criar_projeto(args.criar)
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    if not args.projeto:
        parser.print_help()
        return

    if args.video:
        service.project_name = args.projeto

        if args.etapa == "completo":
            result = service.executar_pipeline_completo(args.video)
        elif args.etapa == "transcrever":
            result = service.transcrever(args.video)
        elif args.etapa == "cenas":
            result = service.gerar_cenas()
        elif args.etapa == "storyboard":
            result = service.gerar_storyboard()
        elif args.etapa == "queries":
            result = service.gerar_queries()
        elif args.etapa == "buscar":
            result = service.buscar_midias()
        elif args.etapa == "renderizar":
            result = service.renderizar()
        else:
            result = {"error": f"Etapa desconhecida: {args.etapa}"}

        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print("Forneça --video para processar")


if __name__ == "__main__":
    main()