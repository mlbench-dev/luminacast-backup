import * as ContextMenuPrimitive from '@radix-ui/react-context-menu';
import * as React from 'react';

const ContextMenu = ContextMenuPrimitive.Root;

const ContextMenuTrigger = ContextMenuPrimitive.Trigger;

const ContextMenuGroup = ContextMenuPrimitive.Group;

const ContextMenuPortal = ContextMenuPrimitive.Portal;

const ContextMenuSub = ContextMenuPrimitive.Sub;

const ContextMenuSubTrigger = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.SubTrigger>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubTrigger> & {
		inset?: boolean;
	}
>(({className, inset, ...props}, ref) => (
	<ContextMenuPrimitive.SubTrigger
		ref={ref}
		className={`hover:bg-editor-starter-accent flex cursor-default items-center rounded-sm px-2 py-1.5 text-sm text-white outline-none select-none hover:text-white ${
			inset ? 'pl-8' : ''
		} ${className || ''}`}
		{...props}
	/>
));
ContextMenuSubTrigger.displayName = ContextMenuPrimitive.SubTrigger.displayName;

const ContextMenuSubContent = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.SubContent>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.SubContent>
>(({className, ...props}, ref) => (
	<ContextMenuPrimitive.SubContent
		ref={ref}
		className={`border-editor-starter-border bg-editor-starter-panel z-50 min-w-[8rem] overflow-hidden rounded-md border p-1 shadow-lg ${className || ''}`}
		{...props}
	/>
));
ContextMenuSubContent.displayName = ContextMenuPrimitive.SubContent.displayName;

const ContextMenuContent = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.Content>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Content>
>(({className, ...props}, ref) => (
	<ContextMenuPrimitive.Portal>
		<ContextMenuPrimitive.Content
			ref={ref}
			className={`bg-editor-starter-panel z-50 min-w-[8rem] overflow-hidden rounded-md p-1 text-white shadow-lg ${className || ''}`}
			{...props}
		/>
	</ContextMenuPrimitive.Portal>
));
ContextMenuContent.displayName = ContextMenuPrimitive.Content.displayName;

const ContextMenuItem = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.Item>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Item> & {
		inset?: boolean;
	}
>(({className, inset, ...props}, ref) => (
	<ContextMenuPrimitive.Item
		ref={ref}
		className={`relative flex cursor-default items-center rounded-sm px-4 py-2 text-xs text-neutral-300 outline-none select-none hover:bg-white/5 hover:text-white data-[disabled]:pointer-events-none data-[disabled]:opacity-50 ${
			inset ? 'pl-8' : ''
		} ${className || ''}`}
		{...props}
	/>
));
ContextMenuItem.displayName = ContextMenuPrimitive.Item.displayName;

const ContextMenuLabel = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.Label>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Label> & {
		inset?: boolean;
	}
>(({className, inset, ...props}, ref) => (
	<ContextMenuPrimitive.Label
		ref={ref}
		className={`px-2 py-1.5 text-sm font-semibold text-white ${
			inset ? 'pl-8' : ''
		} ${className || ''}`}
		{...props}
	/>
));
ContextMenuLabel.displayName = ContextMenuPrimitive.Label.displayName;

const ContextMenuSeparator = React.forwardRef<
	React.ElementRef<typeof ContextMenuPrimitive.Separator>,
	React.ComponentPropsWithoutRef<typeof ContextMenuPrimitive.Separator>
>(({className, ...props}, ref) => (
	<ContextMenuPrimitive.Separator
		ref={ref}
		className={`-mx-1 my-1 h-px bg-white/10 ${className || ''}`}
		{...props}
	/>
));
ContextMenuSeparator.displayName = ContextMenuPrimitive.Separator.displayName;

const ContextMenuShortcut = ({
	className,
	...props
}: React.HTMLAttributes<HTMLSpanElement>) => {
	return (
		<span
			className={`ml-auto text-xs tracking-widest text-slate-400 ${className || ''}`}
			{...props}
		/>
	);
};
ContextMenuShortcut.displayName = 'ContextMenuShortcut';

export {
	ContextMenu,
	ContextMenuContent,
	ContextMenuGroup,
	ContextMenuItem,
	ContextMenuLabel,
	ContextMenuPortal,
	ContextMenuSeparator,
	ContextMenuShortcut,
	ContextMenuSub,
	ContextMenuSubContent,
	ContextMenuSubTrigger,
	ContextMenuTrigger,
};
